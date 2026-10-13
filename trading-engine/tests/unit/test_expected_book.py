"""The end-of-day reconcile on the expected book: the positions a basket was built
on plus every order sent since, at their fills, against IBKR's positions now."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common import clock  # noqa: E402
from src.execution import kill_switch  # noqa: E402
from src.runtime import end_of_day_reconcile as eod  # noqa: E402

AS_OF = date(2026, 10, 13)
MOC = {
    "order_type": "MOC", "tif": "DAY", "auction": "close", "session": "2026-10-13",
    "fills_at": "2026-10-13T16:00:00-04:00", "moc_deadline": "2026-10-13T15:45:00-04:00",
    "summary": "market-on-close, filled at the Tue Oct 13 close",
}  # fmt: skip
SETTLED = datetime(2026, 10, 13, 16, 45, tzinfo=clock.NYSE_TZ)  # IBKR's positions show the close by now
POSITION_COLUMNS = ["symbol", "current_shares", "avg_cost", "sec_type", "multiplier", "market_price"]


def snapshot(**held: tuple[float, float]) -> pd.DataFrame:
    """A positions snapshot as build_paper_basket writes it: symbol=(shares, IBKR mark)."""
    rows = [
        {"symbol": s, "current_shares": q, "avg_cost": px, "sec_type": "STK", "multiplier": 1.0, "market_price": px}
        for s, (q, px) in held.items()
    ]
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


def submission(*rows: tuple) -> pd.DataFrame:
    """The log submit_paper_orders writes: (symbol, action, quantity, order_id, perm_id, status, dollars)."""
    return pd.DataFrame(
        [
            {"symbol": s, "action": a, "quantity": q, "order_id": oid, "perm_id": pid, "status": st,
             "estimated_trade_dollars": d}
            for s, a, q, oid, pid, st, d in rows
        ]
    )  # fmt: skip


def _contract(i: int, symbol: str) -> SimpleNamespace:
    return SimpleNamespace(conId=i, symbol=symbol, localSymbol=symbol, secType="STK", multiplier="")


class FakeIB:
    """What ib_async reports: positions with marks, and the day's fills."""

    def __init__(self, held: dict[str, tuple[float, float]], fills: list = ()) -> None:
        self.held = held
        self._fills = list(fills)

    def positions(self):
        return [
            SimpleNamespace(contract=_contract(i, s), position=q, avgCost=px, account="DU1")
            for i, (s, (q, px)) in enumerate(self.held.items(), start=1)
        ]

    def portfolio(self):
        return [
            SimpleNamespace(contract=_contract(i, s), marketPrice=px)
            for i, (s, (_q, px)) in enumerate(self.held.items(), start=1)
        ]

    def fills(self):
        return self._fills


def fill(symbol: str, qty: float, side: str, perm_id: int, order_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(shares=qty, side=side, permId=perm_id, orderId=order_id),
        contract=SimpleNamespace(symbol=symbol),
    )


class FakeClient:
    def __init__(self, ib: FakeIB, *, down: bool = False) -> None:
        self.ib = ib
        self.down = down
        self.config = SimpleNamespace(host="127.0.0.1", port=4002, account=None)
        self.connections = 0

    def connect(self) -> None:
        if self.down:
            raise ConnectionRefusedError("gateway down")
        self.connections += 1

    def disconnect(self) -> None:
        pass

    def positions(self, account=None):  # noqa: ARG002
        return self.ib.positions()


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "artifacts" / "runs" / "kill_switch.json")
    return tmp_path


def record_basket(root: Path) -> Path:
    """The model at 1,800 shares of SPY and TLT ($100 each), then a rebalance: buy 400 TLT, sell 100 SPY."""
    return eod.record_submission(
        "paper",
        run_id="20261013_153500",
        positions=snapshot(SPY=(1_800, 100.0), TLT=(1_800, 100.0)),
        orders=submission(("SPY", "SELL", 100, 7, 12, "Submitted", 10_000.0), ("TLT", "BUY", 400, 8, 11, "Submitted", 40_000.0)),
        execution=MOC,
        root=root,
    )


def reconcile(root: Path, client: FakeClient, *, now: datetime = SETTLED) -> eod.EodReconcileResult:
    return eod.reconcile_end_of_day(profile="paper", root=root, client=client, now=now, as_of=AS_OF)


def test_a_submission_starts_the_book_from_the_snapshot(root: Path) -> None:
    path = record_basket(root)
    book = json.loads(path.read_text())
    assert book["positions"] == {"SPY": 1800.0, "TLT": 1800.0}
    assert book["unit_values"] == {"SPY": 100.0, "TLT": 100.0}
    assert [(o["symbol"], o["quantity"], o["perm_id"], o["price"]) for o in book["orders"]] == [
        ("SPY", -100.0, "12", 100.0), ("TLT", 400.0, "11", 100.0),
    ]  # fmt: skip
    assert book["orders"][0]["fills_at"] == MOC["fills_at"]


def test_a_second_submission_keeps_the_positions_the_first_was_built_on(root: Path) -> None:
    record_basket(root)
    # a later run (its snapshot already shows the first basket filled) adds its orders only
    eod.record_submission(
        "paper", run_id="20261013_173000", positions=snapshot(SPY=(1_700, 100.0), TLT=(2_200, 100.0)),
        orders=submission(("GLD", "BUY", 50, 9, 13, "Submitted", 5_000.0)), execution=MOC, root=root,
    )  # fmt: skip
    book = eod.load_expected_book("paper", root)
    assert book["positions"] == {"SPY": 1800.0, "TLT": 1800.0}
    assert [o["symbol"] for o in book["orders"]] == ["SPY", "TLT", "GLD"]


def test_a_filled_basket_reconciles_clean_and_resets_the_book(root: Path) -> None:
    record_basket(root)
    fills = [fill("SPY", 100, "SLD", 12, 7), fill("TLT", 400, "BOT", 11, 8)]
    client = FakeClient(FakeIB({"SPY": (1_700, 101.0), "TLT": (2_200, 99.0)}, fills))
    res = reconcile(root, client)
    assert res.ok and res.diffs == [] and not res.material_mismatch and res.skipped is None
    assert client.connections == 1
    assert [o["counted"] for o in res.orders] == ["at the fills IBKR reports"] * 2
    report = json.loads(Path(res.report_path).read_text())
    assert report["expected"] == {"SPY": 1700, "TLT": 2200} == report["actual"]
    assert not kill_switch.is_kill_switch_armed()
    # the book now holds IBKR's positions, at IBKR's marks, with nothing pending
    book = eod.load_expected_book("paper", root)
    assert book["positions"] == {"SPY": 1700.0, "TLT": 2200.0} and book["orders"] == []
    assert book["unit_values"] == {"SPY": 101.0, "TLT": 99.0}
    assert "IBKR's positions" in book["positions_from"]


def test_orders_without_a_reported_fill_count_in_full(root: Path) -> None:
    # IBKR's API keeps the current day's fills: yesterday's close is gone by the next run
    record_basket(root)
    res = reconcile(root, FakeClient(FakeIB({"SPY": (1_700, 100.0), "TLT": (2_200, 100.0)})))
    assert res.ok and res.diffs == []
    assert all(o["counted"].startswith("in full") for o in res.orders)


def test_an_order_that_never_reached_ibkr_is_not_counted(root: Path) -> None:
    eod.record_submission(
        "paper", run_id="r", positions=snapshot(SPY=(1_800, 100.0)),
        orders=submission(("SPY", "SELL", 100, None, None, "ERROR", 10_000.0)), execution=MOC, root=root,
    )  # fmt: skip
    res = reconcile(root, FakeClient(FakeIB({"SPY": (1_800, 100.0)})))
    assert res.ok and res.diffs == []
    assert res.orders[0]["counted"].startswith("not counted")


def test_a_break_worth_the_threshold_arms_the_kill_switch(root: Path) -> None:
    record_basket(root)
    # 200 TLT short of the 2,200 expected: $20,000 at the mark
    res = reconcile(root, FakeClient(FakeIB({"SPY": (1_700, 100.0), "TLT": (2_000, 100.0)})))
    assert res.ok and res.material_mismatch
    (diff,) = res.diffs
    assert (diff["symbol"], diff["expected_qty"], diff["broker_qty"], diff["qty_diff"]) == ("TLT", 2200, 2000, -200)
    assert diff["notional_diff_usd"] == pytest.approx(20_000.0)
    state = kill_switch.read_kill_switch_state()
    assert state.armed and "EOD reconciliation mismatch on 2026-10-13" in state.reason
    assert Path(res.report_path).name in state.reason
    # reported once: the book is IBKR's positions now
    assert eod.load_expected_book("paper", root)["positions"] == {"SPY": 1700.0, "TLT": 2000.0}


def test_a_break_under_the_threshold_is_reported_but_not_armed(root: Path) -> None:
    record_basket(root)
    res = reconcile(root, FakeClient(FakeIB({"SPY": (1_699.5, 100.0), "TLT": (2_200, 100.0)})))
    assert res.ok and not res.material_mismatch
    assert [d["symbol"] for d in res.diffs] == ["SPY"] and res.diffs[0]["notional_diff_usd"] == pytest.approx(50.0)
    assert not kill_switch.is_kill_switch_armed()


def test_a_break_with_no_price_arms(root: Path) -> None:
    record_basket(root)
    held = {"SPY": (1_700, 100.0), "TLT": (2_200, 100.0), "ZZZ": (10, float("nan"))}
    res = reconcile(root, FakeClient(FakeIB(held)))
    assert res.material_mismatch and res.diffs[0]["symbol"] == "ZZZ" and res.diffs[0]["notional_diff_usd"] is None
    assert kill_switch.is_kill_switch_armed()


def test_it_waits_while_the_auction_is_ahead(root: Path) -> None:
    record_basket(root)
    client = FakeClient(FakeIB({"SPY": (1_800, 100.0), "TLT": (1_800, 100.0)}))
    res = reconcile(root, client, now=datetime(2026, 10, 13, 15, 50, tzinfo=clock.NYSE_TZ))
    assert res.ok and res.diffs == [] and res.report_path is None
    assert "Tue Oct 13 16:00 ET" in res.skipped and "16:30 ET" in res.skipped
    assert client.connections == 0
    assert len(eod.load_expected_book("paper", root)["orders"]) == 2  # still pending


def test_an_unreachable_gateway_is_a_failure_never_clean(root: Path) -> None:
    record_basket(root)
    res = reconcile(root, FakeClient(FakeIB({}), down=True))
    assert res.ok is False and "IBKR connect failed" in (res.error or "")
    assert res.report_path is None and not res.material_mismatch
    assert len(eod.load_expected_book("paper", root)["orders"]) == 2


def test_no_book_is_nothing_to_reconcile(root: Path) -> None:
    res = reconcile(root, FakeClient(FakeIB({"SPY": (1, 100.0)})))
    assert res.ok and res.skipped.startswith("no expected book yet")


def test_a_corrupt_book_is_a_failure(root: Path) -> None:
    path = eod.book_path("paper", root)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    res = reconcile(root, FakeClient(FakeIB({})))
    assert res.ok is False and "unreadable" in res.error and path.name in res.error


def test_a_rerun_on_an_armed_switch_keeps_the_first_reason(root: Path) -> None:
    kill_switch.arm_kill_switch("first arm reason", by="initial")
    before = kill_switch.read_kill_switch_state()
    record_basket(root)
    res = reconcile(root, FakeClient(FakeIB({"SPY": (1_700, 100.0), "TLT": (2_000, 100.0)})))
    assert res.material_mismatch
    after = kill_switch.read_kill_switch_state()
    assert (after.reason, after.armed_at, after.last_change_by) == (before.reason, before.armed_at, before.last_change_by)
