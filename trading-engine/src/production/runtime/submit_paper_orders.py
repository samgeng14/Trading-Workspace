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


from src.broker.ibkr.client import IBKRClient, IBKRConnectionConfig
from src.broker.ibkr.settings import resolve_endpoint
from src.broker.ibkr.orders import IBKROrderManager, OrderRequest


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
    order_type: str = "MKT"
    wait_for_status: bool = True
    timeout: float = 30.0
    symbols: tuple[str, ...] = ()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


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
        order_type="MKT",
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

    return orders


def build_trade_plan(orders_df: pd.DataFrame, account: str | None, order_type: str) -> list[OrderRequest]:
    requests: list[OrderRequest] = []
    for _, row in orders_df.iterrows():
        requests.append(
            OrderRequest(
                symbol=str(row["symbol"]),
                action=str(row["side"]),
                quantity=float(int(row["quantity"])),
                order_type=order_type,
                account=account,
                order_ref=f"paper-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{row['symbol']}",
            )
        )
    return requests


def _plan_rows(orders_df: pd.DataFrame, order_type: str, dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in orders_df.iterrows():
        rows.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "symbol": row["symbol"],
                "action": row["side"],
                "order_type": order_type,
                "quantity": int(row["quantity"]),
                "delta_shares": int(row["delta_shares"]),
                "estimated_trade_dollars": float(row.get("estimated_trade_dollars", 0.0)),
                "status": "DRY_RUN" if dry_run else "READY",
            }
        )
    return rows


def submit_paper_orders(config: SubmitPaperOrdersConfig) -> dict[str, Any]:
    orders_df = load_orders(config.orders_path, symbols=config.symbols)
    config.submissions_dir.mkdir(parents=True, exist_ok=True)
    config.fills_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    submission_path = config.submissions_dir / f"paper_orders_submitted_{timestamp}.csv"
    fills_path = config.fills_dir / f"paper_trade_log_{timestamp}.csv"

    if config.dry_run:
        preview_df = pd.DataFrame(_plan_rows(orders_df, config.order_type, dry_run=True))
        preview_df.to_csv(submission_path, index=False)
        return {
            "submitted": False,
            "submission_path": submission_path,
            "fills_path": None,
            "rows": int(len(preview_df)),
        }

    client = IBKRClient(
        IBKRConnectionConfig(
            host=config.host,
            port=config.port,
            client_id=config.client_id,
            account=config.account,
            expected_profile="paper",
        )
    )
    client.connect()
    manager = IBKROrderManager(client)

    submission_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    try:
        requests = build_trade_plan(orders_df, account=config.account, order_type=config.order_type)

        for request, (_, order_row) in zip(requests, orders_df.iterrows()):
            try:
                trade = manager.place_order(
                    request,
                    wait_for_status=config.wait_for_status,
                    timeout=config.timeout,
                )
                status = getattr(trade.orderStatus, "status", None)
                summary = manager.summarize_trade(trade)
                submission_rows.append(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "symbol": request.symbol,
                        "action": request.action,
                        "order_type": request.order_type,
                        "quantity": int(request.quantity),
                        "delta_shares": int(order_row["delta_shares"]),
                        "estimated_trade_dollars": float(order_row.get("estimated_trade_dollars", 0.0)),
                        "status": status,
                        "order_id": summary.get("order_id"),
                        "perm_id": summary.get("perm_id"),
                    }
                )
                fill_rows.append(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        **summary,
                    }
                )
            except Exception as exc:
                submission_rows.append(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "symbol": request.symbol,
                        "action": request.action,
                        "order_type": request.order_type,
                        "quantity": int(request.quantity),
                        "delta_shares": int(order_row["delta_shares"]),
                        "estimated_trade_dollars": float(order_row.get("estimated_trade_dollars", 0.0)),
                        "status": "ERROR",
                        "error": str(exc),
                    }
                )

        pd.DataFrame(submission_rows).to_csv(submission_path, index=False)
        pd.DataFrame(fill_rows).to_csv(fills_path, index=False)
    finally:
        client.disconnect()

    return {
        "submitted": True,
        "submission_path": submission_path,
        "fills_path": fills_path,
        "rows": int(len(submission_rows)),
    }


def parse_args() -> argparse.Namespace:
    default = default_config()
    parser = argparse.ArgumentParser(description="Submit paper IBKR orders from paper_orders_only parquet/csv")
    parser.add_argument("--orders-path", type=Path, default=default.orders_path)
    parser.add_argument("--host", default=default.host)
    parser.add_argument("--port", type=int, default=default.port)
    parser.add_argument("--client-id", type=int, default=default.client_id)
    parser.add_argument("--account", default=default.account)
    parser.add_argument("--order-type", choices=["MKT", "LMT", "STP", "MOC"], default=default.order_type)
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
    config.order_type = args.order_type
    config.timeout = args.timeout
    config.wait_for_status = args.wait_for_status
    config.symbols = tuple(args.symbols or [])
    config.dry_run = not args.submit

    result = submit_paper_orders(config)
    print("Submitted:" if result["submitted"] else "Dry run only:", result["submission_path"])
    if result["fills_path"]:
        print("Fills log:", result["fills_path"])
    print("Rows:", result["rows"])


if __name__ == "__main__":
    main()
