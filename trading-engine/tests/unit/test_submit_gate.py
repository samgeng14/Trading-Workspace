"""Every order leaves through submit_paper_orders(). The kill switch and the C++
no-leverage check sit inside it, before it connects, so no caller can skip them:
not the CLI's --submit, not the order service, not the daily runner."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.risk")

from src.broker.ibkr.orders import IBKROrderManager  # noqa: E402
from src.execution import kill_switch  # noqa: E402
from src.production.runtime import order_service  # noqa: E402
from src.production.runtime import submit_paper_orders as spo  # noqa: E402

NAV = 1_000_000.0
# five names at 18% of NAV each, $100 a share, with $100,000 of cash
MODEL = [(s, 1_800, 100.0) for s in ("SPY", "QQQ", "IWM", "TLT", "GLD")]
FULLY_INVESTED = [(s, 2_000, px) for s, _, px in MODEL]
ORDER_COLUMNS = ["symbol", "close", "delta_shares", "side", "estimated_trade_dollars", "estimated_commission"]


def write_run(root: Path, positions, orders, *, cash: float) -> Path:
    """The files a run leaves behind: the positions snapshot, the account summary and
    the orders file. ``positions`` are (symbol, shares, IBKR mark); ``orders`` are
    (symbol, signed shares, price)."""
    broker = root / "data" / "broker"
    for sub in ("positions", "account", "orders", "fills", "reconciliations"):
        (broker / sub).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"symbol": s, "current_shares": q, "avg_cost": px, "sec_type": "STK", "multiplier": 1.0, "market_price": px}
         for s, q, px in positions],
        columns=["symbol", "current_shares", "avg_cost", "sec_type", "multiplier", "market_price"],
    ).to_csv(broker / "positions" / "paper_positions_snapshot.csv", index=False)
    (broker / "account" / "paper_account_summary_20261013_153510.csv").write_text(
        f"tag,value\nNetLiquidation,{NAV}\nTotalCashValue,{cash}\n"
    )
    path = broker / "orders" / "paper_orders_only.parquet"
    pd.DataFrame(
        [{"symbol": s, "close": px, "delta_shares": q, "side": "BUY" if q > 0 else "SELL",
          "estimated_trade_dollars": abs(q) * px, "estimated_commission": 0.0} for s, q, px in orders],
        columns=ORDER_COLUMNS,
    ).to_parquet(path, index=False)
    return path


class FakeClient:
    """Records every connection; a blocked batch must stop before one is made.
    ``open_orders`` is what IBKR reports as open, from every client."""

    connected: list[object] = []
    open_orders: list[object] = []
    open_orders_error: Exception | None = None

    def __init__(self, config) -> None:
        self.config = config

        def all_open():
            if FakeClient.open_orders_error is not None:
                raise FakeClient.open_orders_error
            return list(FakeClient.open_orders)

        self.ib = SimpleNamespace(reqAllOpenOrders=all_open)

    def connect(self) -> None:
        FakeClient.connected.append(self.config)

    def disconnect(self) -> None:
        pass


class FakeManager:
    placed: list[object] = []

    def __init__(self, client) -> None:
        self.client = client

    def place_order(self, request, *, wait_for_status=True, timeout=30.0):  # noqa: ARG002
        FakeManager.placed.append(request)
        return SimpleNamespace(
            contract=SimpleNamespace(symbol=request.symbol),
            order=SimpleNamespace(
                orderId=1, permId=11, action=request.action, orderType=request.order_type,
                totalQuantity=request.quantity,
            ),
            orderStatus=SimpleNamespace(status="Submitted"),
            fills=[],
        )

    summarize_trade = staticmethod(IBKROrderManager.summarize_trade)


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(spo, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(spo, "IBKRClient", FakeClient)
    monkeypatch.setattr(spo, "IBKROrderManager", FakeManager)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "artifacts" / "runs" / "kill_switch.json")
    FakeClient.connected.clear()
    FakeClient.open_orders = []
    FakeClient.open_orders_error = None
    FakeManager.placed.clear()
    return tmp_path


def config_for(root: Path, orders_path: Path) -> spo.SubmitPaperOrdersConfig:
    return spo.SubmitPaperOrdersConfig(
        orders_path=orders_path,
        submissions_dir=root / "data" / "broker" / "orders",
        fills_dir=root / "data" / "broker" / "fills",
        dry_run=False,
        wait_for_status=False,
    )


def submissions(root: Path) -> list[Path]:
    return sorted((root / "data" / "broker" / "orders").glob("paper_orders_submitted_*.csv"))


def test_an_armed_kill_switch_sends_nothing(run_root):
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0)], cash=100_000)
    kill_switch.arm_kill_switch("ops review", by="pytest")
    out = spo.submit_paper_orders(config_for(run_root, orders))
    assert out["submitted"] is False and out["blocked"] is True
    assert out["reasons"][0].startswith("Kill switch is ARMED: ops review")
    assert FakeClient.connected == [] and FakeManager.placed == []
    assert submissions(run_root) == []


def test_a_book_that_ends_levered_sends_nothing(run_root):
    # fully invested, then a $100,000 buy with no cash: a margin loan and 110% gross
    orders = write_run(run_root, FULLY_INVESTED, [("TLT", 1_000, 100.0)], cash=0.0)
    out = spo.submit_paper_orders(config_for(run_root, orders))
    assert out["blocked"] is True
    assert out["reasons"] and all(r.startswith("no leverage: ") for r in out["reasons"])
    assert any("gross 110.0%" in r for r in out["reasons"])
    assert FakeClient.connected == [] and FakeManager.placed == []
    assert submissions(run_root) == []


def test_an_unlevered_batch_goes_out_sells_first(run_root):
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0), ("SPY", -100, 100.0)], cash=100_000)
    out = spo.submit_paper_orders(config_for(run_root, orders))
    assert out["submitted"] is True and out["rows"] == 2 and not out.get("blocked")
    assert len(FakeClient.connected) == 1
    sent = [(r.symbol, r.action, r.quantity) for r in FakeManager.placed]
    assert sent == [("SPY", "SELL", 100.0), ("TLT", "BUY", 400.0)]
    assert len(submissions(run_root)) == 1


def test_a_symbol_filter_is_checked_on_what_it_sends(run_root):
    # the whole file keeps the book inside the mandate; the two buys alone, without
    # the sell that pays for them, would borrow $20,000
    batch = [("TLT", 600, 100.0), ("QQQ", 600, 100.0), ("SPY", -1_000, 100.0)]
    orders = write_run(run_root, MODEL, batch, cash=100_000)
    cfg = config_for(run_root, orders)
    assert spo.submit_paper_orders(cfg)["submitted"] is True
    FakeManager.placed.clear()
    cfg.symbols = ("TLT", "QQQ")
    out = spo.submit_paper_orders(cfg)
    assert out["blocked"] is True and FakeManager.placed == []
    assert any("margin loan" in r for r in out["reasons"]), out["reasons"]


def test_the_order_service_reports_a_block_and_locks_only_what_was_sent(run_root, monkeypatch):
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0)], cash=100_000)
    broker = run_root / "data" / "broker"
    monkeypatch.setattr(order_service, "ORDERS_DIR", broker / "orders")
    monkeypatch.setattr(order_service, "FILLS_DIR", broker / "fills")
    monkeypatch.setattr(order_service, "RECON_DIR", broker / "reconciliations")
    monkeypatch.setattr(order_service, "BASKETS_DIR", run_root / "artifacts" / "baskets")
    monkeypatch.setattr(order_service, "RUNS_DIR", run_root / "artifacts" / "runs")
    monkeypatch.setattr(spo, "default_config", lambda: config_for(run_root, orders))

    kill_switch.arm_kill_switch("ops review", by="pytest")
    out = order_service.submit_paper_orders(profile="paper", force=True)
    assert out["ok"] is False and out["submitted"] is False and out["blocked"] is True
    assert "Kill switch is ARMED" in out["reason"]
    assert order_service.read_submit_lock("paper") is None

    # a dry run previews the batch; it doesn't mark the basket as sent
    kill_switch.disarm_kill_switch(by="pytest")
    out = order_service.submit_paper_orders(profile="paper", force=True, dry_run=True)
    assert out["ok"] is True and out["submitted"] is False
    assert order_service.read_submit_lock("paper") is None

    # the real send does, and the guard then refuses the same basket
    out = order_service.submit_paper_orders(profile="paper")
    assert out["ok"] is True and out["submitted"] is True
    assert order_service.read_submit_lock("paper") is not None
    assert order_service.submit_paper_orders(profile="paper")["ok"] is False


# ── a second batch never goes out on top of the first ────────────────────


def open_order(symbol: str, action: str, qty: float, status: str, client_id: int = 300) -> SimpleNamespace:
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=symbol),
        order=SimpleNamespace(action=action, totalQuantity=qty, clientId=client_id),
        orderStatus=SimpleNamespace(status=status),
    )


def test_a_second_batch_waits_while_the_first_is_working(run_root):
    # 15:35's market-on-close sell is still working; a forced re-run builds the same trade again
    FakeClient.open_orders = [open_order("SPY", "SELL", 100, "PreSubmitted")]
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0), ("SPY", -100, 100.0)], cash=100_000)
    out = spo.submit_paper_orders(config_for(run_root, orders))
    assert out["submitted"] is False and out["blocked"] is True
    assert "working orders on this batch's symbols: SELL 100 SPY (PreSubmitted, client 300)" in out["reasons"][0]
    assert FakeManager.placed == [] and submissions(run_root) == []


def test_working_orders_elsewhere_or_done_ones_dont_block(run_root):
    FakeClient.open_orders = [
        open_order("GLD", "BUY", 5, "Submitted", client_id=0),  # another symbol, placed in TWS
        open_order("SPY", "SELL", 100, "Filled"),
        open_order("TLT", "BUY", 400, "Cancelled"),
    ]
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0), ("SPY", -100, 100.0)], cash=100_000)
    out = spo.submit_paper_orders(config_for(run_root, orders))
    assert out["submitted"] is True and len(FakeManager.placed) == 2


def test_an_unchecked_book_sends_nothing(run_root):
    FakeClient.open_orders_error = TimeoutError()
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0)], cash=100_000)
    out = spo.submit_paper_orders(config_for(run_root, orders))
    assert out["blocked"] is True and out["reasons"][0].startswith("could not check IBKR for working orders")
    assert FakeManager.placed == []


# ── a slow acknowledgment is not an error ─────────────────────────────────


class SlowAckManager(FakeManager):
    """The VM's gateway on 2026-10-21: every order sent, none acknowledged while the
    batch went out (all 9 filled at the close). IBKR gives a permId on acknowledging."""

    calls: list[str] = []

    def place_order(self, request, *, wait_for_status=True, timeout=30.0):
        SlowAckManager.calls.append(f"place {request.symbol}")
        trade = super().place_order(request, wait_for_status=wait_for_status, timeout=timeout)
        trade.orderStatus.status, trade.order.permId = "PendingSubmit", 0
        return trade

    def wait_for_acknowledgments(self, trades, *, timeout=30.0, poll_interval=0.5):  # noqa: ARG002
        SlowAckManager.calls.append(f"wait {len(trades)}")
        return [t.orderStatus.status for t in trades]


def test_the_batch_goes_out_before_one_wait_and_an_unacknowledged_order_counts_as_sent(run_root, monkeypatch):
    from src.runtime.end_of_day_reconcile import expected_positions

    monkeypatch.setattr(spo, "IBKROrderManager", SlowAckManager)
    SlowAckManager.calls = []
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0), ("SPY", -100, 100.0)], cash=100_000)
    cfg = config_for(run_root, orders)
    cfg.wait_for_status = True
    out = spo.submit_paper_orders(cfg)
    assert out["submitted"] is True
    assert SlowAckManager.calls == ["place SPY", "place TLT", "wait 2"]  # not a wait after each order
    log = pd.read_csv(submissions(run_root)[0])
    assert list(log["status"]) == ["PendingSubmit", "PendingSubmit"] and list(log["order_id"]) == [1, 1]
    assert log["error"].str.startswith("sent, but IBKR hadn't acknowledged it 30s later").all()
    # the reconcile counts a sent order: it skipped them as "never reached IBKR" before
    book = {"positions": {"SPY": 1_800, "TLT": 1_800}, "orders": [
        {"symbol": r.symbol, "quantity": r.delta_shares, "status": r.status, "order_id": "1", "perm_id": None}
        for r in log.itertuples()]}  # fmt: skip
    expected, counted = expected_positions(book, fills=[])
    assert expected == {"SPY": 1_700, "TLT": 2_200}
    assert all(c["counted"].startswith("in full") for c in counted)


def test_an_order_that_never_went_out_is_an_error_and_the_rest_go(run_root, monkeypatch):
    class Refused(FakeManager):
        def place_order(self, request, **kw):
            if request.symbol == "TLT":
                raise ValueError("No security definition has been found for the request")
            return super().place_order(request, **kw)

    monkeypatch.setattr(spo, "IBKROrderManager", Refused)
    orders = write_run(run_root, MODEL, [("TLT", 400, 100.0), ("SPY", -100, 100.0)], cash=100_000)
    spo.submit_paper_orders(config_for(run_root, orders))
    log = pd.read_csv(submissions(run_root)[0]).set_index("symbol")
    assert log.loc["SPY", "status"] == "Submitted" and log.loc["TLT", "status"] == "ERROR"
    assert log.loc["TLT", "error"] == "No security definition has been found for the request"


def test_the_wait_is_one_wait_for_the_whole_batch():
    from datetime import datetime, timedelta, timezone

    from src.broker.ibkr.orders import acknowledged_after

    trades = [SimpleNamespace(orderStatus=SimpleNamespace(status="PendingSubmit")) for _ in range(9)]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            trades[0].orderStatus.status = "PreSubmitted"

    statuses = IBKROrderManager(SimpleNamespace(sleep=sleep)).wait_for_acknowledgments(
        trades, timeout=2.0, poll_interval=0.5
    )
    assert sleeps == [0.5] * 4  # 2 s for all nine, where it was 30 s for each
    assert statuses == ["PreSubmitted"] + ["PendingSubmit"] * 8

    sent = datetime(2026, 10, 21, 19, 35, 13, tzinfo=timezone.utc)
    log = [SimpleNamespace(time=sent, status="PendingSubmit"),
           SimpleNamespace(time=sent + timedelta(seconds=0.4), status="PreSubmitted")]  # fmt: skip
    assert acknowledged_after(SimpleNamespace(log=log)) == 0.4
    assert acknowledged_after(SimpleNamespace(log=log[:1])) is None


def test_each_orders_timeline_from_ibkr_is_kept(run_root, monkeypatch, caplog):
    import json
    import logging
    from datetime import datetime, timedelta, timezone

    sent = datetime(2026, 10, 22, 19, 35, 14, tzinfo=timezone.utc)

    class Talkative(FakeManager):
        def place_order(self, request, **kw):
            trade = super().place_order(request, **kw)
            trade.log = [
                SimpleNamespace(time=sent, status="PendingSubmit", message="", errorCode=0),
                SimpleNamespace(time=sent + timedelta(seconds=31.2), status="PendingSubmit", errorCode=399,
                                message="Order Message: warning, held until the closing auction"),
                SimpleNamespace(time=sent + timedelta(seconds=31.9), status="PreSubmitted", message="", errorCode=0),
            ]  # fmt: skip
            return trade

    monkeypatch.setattr(spo, "IBKROrderManager", Talkative)
    orders = write_run(run_root, MODEL, [("SPY", -100, 100.0)], cash=100_000)
    with caplog.at_level(logging.INFO):
        spo.submit_paper_orders(config_for(run_root, orders))
    log = pd.read_csv(submissions(run_root)[0])
    said = json.loads(log.loc[0, "status_log"])
    assert [e["status"] for e in said] == ["PendingSubmit", "PendingSubmit", "PreSubmitted"]
    assert said[1] == {"s": 31.2, "status": "PendingSubmit", "code": 399,
                       "message": "Order Message: warning, held until the closing auction"}  # fmt: skip
    assert log.loc[0, "acknowledged_s"] == 31.9
    assert "SELL 100 SPY: PendingSubmit at 0.0s → PendingSubmit at 31.2s (Order Message" in caplog.text
