from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.broker.ibkr.auction import Execution, execution_for  # noqa: E402
from src.broker.ibkr.client import IBKRClient, IBKRConnectionConfig  # noqa: E402
from src.broker.ibkr.orders import (  # noqa: E402
    UNACKNOWLEDGED,
    IBKROrderManager,
    OrderRequest,
    acknowledged_after,
    trade_status,
)
from src.broker.ibkr.settings import resolve_endpoint  # noqa: E402
from src.execution.kill_switch import KillSwitchTripped, require_kill_switch_clear  # noqa: E402
from src.runtime.leverage import check_leverage  # noqa: E402


@dataclass(slots=True)
class SubmitPaperOrdersConfig:
    orders_path: Path
    submissions_dir: Path
    fills_dir: Path
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 300
    account: str | None = None
    dry_run: bool = True
    wait_for_status: bool = True
    timeout: float = 30.0
    symbols: tuple[str, ...] = ()


def default_config() -> SubmitPaperOrdersConfig:
    root = _project_root()
    endpoint = resolve_endpoint("paper")
    return SubmitPaperOrdersConfig(
        orders_path=root / "data/broker/orders/paper_orders_only.parquet",
        submissions_dir=root / "data/broker/orders",
        fills_dir=root / "data/broker/fills",
        host=endpoint.host,
        port=endpoint.port,
        client_id=endpoint.client_id("orders"),
        account=endpoint.account,
        dry_run=True,
        wait_for_status=True,
        timeout=30.0,
    )


def load_orders(orders_path: Path, symbols: tuple[str, ...] = ()) -> pd.DataFrame:
    if not orders_path.exists():
        raise FileNotFoundError(f"Orders file not found: {orders_path}")

    if orders_path.suffix == ".parquet":
        orders = pd.read_parquet(orders_path)
    else:
        orders = pd.read_csv(orders_path)

    required = {"symbol", "side", "delta_shares"}
    missing = required - set(orders.columns)
    if missing:
        raise ValueError(f"Orders file missing columns: {sorted(missing)}")

    orders = orders.copy()
    orders["symbol"] = orders["symbol"].astype(str).str.upper()

    if symbols:
        keep = {s.strip().upper() for s in symbols}
        orders = orders.loc[orders["symbol"].isin(keep)].copy()

    orders = orders.loc[orders["side"].isin(["BUY", "SELL"])].copy()
    orders["quantity"] = orders["delta_shares"].abs().astype(int)
    orders = orders.loc[orders["quantity"] > 0].reset_index(drop=True)

    if orders.empty:
        raise ValueError("No executable orders found after filtering")

    return sells_first(orders)


def sells_first(orders: pd.DataFrame) -> pd.DataFrame:
    """Every SELL ahead of every BUY, each side in file order.

    The no-leverage check judges the book after every order fills, and the
    basket (build_reconciliation) lists BUYs first. Sending the sells first
    puts them at IBKR ahead of the buys that spend their proceeds. This orders
    the submissions only; nothing waits for fills. It is done here, where every
    submit path (the CLI, the dashboard, the daily runner) reads the orders
    file, rather than by re-sorting the reconciliation files, which the
    dashboard and the run's checks read as written.
    """
    order = orders["side"].map({"SELL": 0, "BUY": 1})
    return orders.iloc[order.argsort(kind="stable")].reset_index(drop=True)


def build_trade_plan(orders_df: pd.DataFrame, account: str | None, execution: Execution) -> list[OrderRequest]:
    requests: list[OrderRequest] = []
    for _, row in orders_df.iterrows():
        requests.append(
            OrderRequest(
                symbol=str(row["symbol"]),
                action=str(row["side"]),
                quantity=float(int(row["quantity"])),
                order_type=execution.order_type,
                tif=execution.tif,
                account=account,
                order_ref=f"paper-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{row['symbol']}",
            )
        )
    return requests


def _plan_rows(orders_df: pd.DataFrame, execution: Execution, dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in orders_df.iterrows():
        rows.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "symbol": row["symbol"],
                "action": row["side"],
                "order_type": execution.order_type,
                "tif": execution.tif,
                "quantity": int(row["quantity"]),
                "delta_shares": int(row["delta_shares"]),
                "estimated_trade_dollars": float(row.get("estimated_trade_dollars", 0.0)),
                "status": "DRY_RUN" if dry_run else "READY",
            }
        )
    return rows


PROFILE = "paper"  # the only profile this path sends; the client refuses a live account

# IBKR order states in which an order can still fill (PendingCancel too: a cancel
# can lose the race with a fill).
WORKING = {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted", "ApiUpdate", "PendingCancel"}


def working_orders(client: Any, symbols: Any) -> list[str]:
    """IBKR's working orders, from every client, on any of ``symbols``, as
    "SELL 100 SPY (Submitted, client 300)".

    A batch is built from positions, and positions don't show an order until it
    fills: a market-on-close basket sent at 15:35 is still working until 16:00.
    A second batch for the same day (a re-run, a forced rebalance, a run after
    one crashed mid-send) would trade the same differences again.
    """
    wanted = {str(s).upper().strip() for s in symbols}
    out = []
    for trade in client.ib.reqAllOpenOrders():
        symbol = str(getattr(trade.contract, "symbol", "") or "").upper()
        status = str(getattr(trade.orderStatus, "status", "") or "")
        if symbol in wanted and status in WORKING:
            o = trade.order
            out.append(f"{o.action} {float(o.totalQuantity):g} {symbol} ({status}, client {o.clientId})")
    return out


def pre_trade_blocks(orders_df: pd.DataFrame, orders_path: Path, *, profile: str = PROFILE) -> list[str]:
    """Why ``orders_df`` must not go out, or [] when it may.

    The kill switch, then the C++ no-leverage check on the whole book after
    these orders (src/runtime/leverage.py). submit_paper_orders runs this
    before it connects, so every caller gets it: the CLI's --submit, the order
    service the dashboard and the daily runner go through, and anything new.
    The runner runs the same checks earlier, with the exposure limits, and logs
    the full report; this is the one that can't be skipped.
    """
    try:
        require_kill_switch_clear()
    except KillSwitchTripped as exc:
        return [str(exc)]
    leverage = check_leverage(profile, root=_project_root(), orders_path=orders_path, orders=orders_df)
    return list(leverage.blocking_reasons)


def submit_paper_orders(config: SubmitPaperOrdersConfig) -> dict[str, Any]:
    orders_df = load_orders(config.orders_path, symbols=config.symbols)
    config.submissions_dir.mkdir(parents=True, exist_ok=True)
    config.fills_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    submission_path = config.submissions_dir / f"paper_orders_submitted_{timestamp}.csv"
    fills_path = config.fills_dir / f"paper_trade_log_{timestamp}.csv"
    # MOC before the closing-auction deadline, else market-on-open for the next
    # session (src/broker/ibkr/auction.py). Decided once, as the batch goes out,
    # so its sells and buys fill in the same auction.
    execution = execution_for(orders_df["symbol"])

    if config.dry_run:
        preview_df = pd.DataFrame(_plan_rows(orders_df, execution, dry_run=True))
        preview_df.to_csv(submission_path, index=False)
        return {
            "submitted": False,
            "submission_path": submission_path,
            "fills_path": None,
            "rows": int(len(preview_df)),
            "execution": execution.as_dict(),
        }

    reasons = pre_trade_blocks(orders_df, config.orders_path)
    if reasons:
        return {
            "submitted": False,
            "blocked": True,
            "reasons": reasons,
            "submission_path": None,
            "fills_path": None,
            "rows": 0,
            "execution": execution.as_dict(),
        }

    client = IBKRClient(
        IBKRConnectionConfig(
            host=config.host,
            port=config.port,
            client_id=config.client_id,
            account=config.account,
            expected_profile=PROFILE,
        )
    )
    client.connect()
    try:
        working = working_orders(client, orders_df["symbol"])
        working_error = None
    except Exception as exc:  # noqa: BLE001 — unverified is not clear: send nothing
        working, working_error = [], f"{type(exc).__name__}: {exc}"
    if working or working_error:
        client.disconnect()
        reason = (
            f"IBKR already has working orders on this batch's symbols: {', '.join(working)}. "
            "A second batch would trade the same differences again (positions don't show an order "
            "until it fills). Let them fill (a market-on-close order fills at the close) or cancel "
            "them (bin/tradeit kill), then run again."
            if working
            else f"could not check IBKR for working orders ({working_error}), so nothing was sent"
        )
        return {
            "submitted": False,
            "blocked": True,
            "reasons": [reason],
            "submission_path": None,
            "fills_path": None,
            "rows": 0,
            "execution": execution.as_dict(),
        }
    manager = IBKROrderManager(client)

    submission_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    try:
        requests = build_trade_plan(orders_df, account=config.account, execution=execution)

        # Send the whole batch, then wait once for IBKR's acknowledgments. Waiting
        # after each order cost 30 s apiece on 2026-10-21, when the gateway was slow
        # to acknowledge: 9 orders took 4.5 minutes, and a full basket sent that way
        # would run past the 15:50 auction cutoff.
        sent: list[tuple[dict[str, Any], Any]] = []
        for request, (_, order_row) in zip(requests, orders_df.iterrows()):
            row: dict[str, Any] = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "symbol": request.symbol,
                "action": request.action,
                "order_type": request.order_type,
                "tif": request.tif,
                "quantity": int(request.quantity),
                "delta_shares": int(order_row["delta_shares"]),
                "estimated_trade_dollars": float(order_row.get("estimated_trade_dollars", 0.0)),
            }
            submission_rows.append(row)
            try:
                trade = manager.place_order(request, wait_for_status=False)
            except Exception as exc:  # never sent: building or qualifying it failed
                row.update(status="ERROR", error=str(exc))
                continue
            sent.append((row, trade))

        if config.wait_for_status and sent:
            manager.wait_for_acknowledgments([trade for _, trade in sent], timeout=config.timeout)
        for row, trade in sent:
            summary = manager.summarize_trade(trade)
            status = trade_status(trade)
            row.update(
                status=status,
                order_id=summary.get("order_id"),
                perm_id=summary.get("perm_id"),
                acknowledged_s=acknowledged_after(trade),
            )
            # sent, so not an error the reconcile skips: IBKR has it unless it says otherwise
            if config.wait_for_status and status in UNACKNOWLEDGED:
                row["error"] = (
                    f"sent, but IBKR hadn't acknowledged it {config.timeout:.0f}s later "
                    f"(status {status or 'none'}); the reconcile counts it as placed"
                )
            fill_rows.append({"timestamp": datetime.now().isoformat(timespec="seconds"), **summary})

        pd.DataFrame(submission_rows).to_csv(submission_path, index=False)
        pd.DataFrame(fill_rows).to_csv(fills_path, index=False)
    finally:
        client.disconnect()

    return {
        "submitted": True,
        "submission_path": submission_path,
        "fills_path": fills_path,
        "rows": int(len(submission_rows)),
        "execution": execution.as_dict(),
    }


def parse_args() -> argparse.Namespace:
    default = default_config()
    parser = argparse.ArgumentParser(description="Submit paper IBKR orders from paper_orders_only parquet/csv")
    parser.add_argument("--orders-path", type=Path, default=default.orders_path)
    parser.add_argument("--host", default=default.host)
    parser.add_argument("--port", type=int, default=default.port)
    parser.add_argument("--client-id", type=int, default=default.client_id)
    parser.add_argument("--account", default=default.account)
    parser.add_argument("--timeout", type=float, default=default.timeout)
    parser.add_argument("--wait-for-status", action="store_true", default=default.wait_for_status)
    parser.add_argument("--symbol", action="append", dest="symbols", default=[])
    parser.add_argument("--submit", action="store_true", help="Actually submit orders. Default is dry run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = default_config()
    config.orders_path = args.orders_path
    config.host = args.host
    config.port = args.port
    config.client_id = args.client_id
    config.account = args.account
    config.timeout = args.timeout
    config.wait_for_status = args.wait_for_status
    config.symbols = tuple(args.symbols or [])
    config.dry_run = not args.submit

    result = submit_paper_orders(config)
    if result.get("blocked"):
        print("Blocked: nothing was sent.")
        for reason in result["reasons"]:
            print(" ", reason)
        sys.exit(1)
    print("Submitted:" if result["submitted"] else "Dry run only:", result["submission_path"])
    if result["fills_path"]:
        print("Fills log:", result["fills_path"])
    print("Rows:", result["rows"])
    print("Orders:", result["execution"]["summary"])


if __name__ == "__main__":
    main()
