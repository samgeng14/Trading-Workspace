"""End-to-end smoke test for the runtime hot path.

Walks one full slice of the daily cycle with a fake broker sink:

    decide_rebalance  ->  build target book  ->  size positions  ->
    risk gate (kill switch + pre-trade)  ->  order policy  ->
    order router  ->  fake broker sink  ->  audit trail on disk  ->
    end-of-day reconcile

Each step must succeed and leave the right artefacts behind. The whole
chain runs in a tmp_path-isolated filesystem so it can't pollute the
real ``artifacts/runs/`` or ``data/broker/reconciliations/``.

This is the test that fails first when we accidentally break the
contract between two adjacent layers.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
for p in (str(PROJECT_ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.execution.kill_switch import (  # noqa: E402
    arm_kill_switch,
    disarm_kill_switch,
    is_kill_switch_armed,
    read_kill_switch_state,
)
from src.execution.order_policy import OrderPolicy, OrderTicket  # noqa: E402
from src.execution.order_router import submit_orders  # noqa: E402
from src.portfolio.sizing import size_basket  # noqa: E402
from src.portfolio.target_builder import (  # noqa: E402
    TargetBookConfig,
    build_target_book,
)
from src.common import clock  # noqa: E402
from src.production.runtime.build_paper_basket import POSITION_COLUMNS  # noqa: E402
from src.runtime.auto_rebalance import decide_rebalance  # noqa: E402
from src.runtime.end_of_day_reconcile import record_submission, reconcile_end_of_day  # noqa: E402
from src.runtime.risk_checks import run_pre_trade_risk_checks  # noqa: E402
from tests.unit.test_expected_book import FakeClient, FakeIB  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    """Redirect every persisted-state path to tmp_path."""
    ks = tmp_path / "kill_switch.json"
    sub_dir = tmp_path / "submissions"
    recon_dir = tmp_path / "reconciliations"
    sub_dir.mkdir()
    recon_dir.mkdir()

    monkeypatch.setattr("src.execution.kill_switch.KILL_SWITCH_PATH", ks)
    monkeypatch.setattr("src.execution.order_router.SUBMISSION_LOG_DIR", sub_dir)
    monkeypatch.setattr("src.runtime.end_of_day_reconcile.RECON_DIR", recon_dir)

    yield {
        "kill_switch_path": ks,
        "submissions_dir": sub_dir,
        "recon_dir": recon_dir,
        "tmp": tmp_path,
    }


# ─────────────────────────────────────────────────────────────────────────
# Happy path — one clean cycle from decide-rebalance to audit trail
# ─────────────────────────────────────────────────────────────────────────


def test_full_cycle_happy_path(isolated_runtime, monkeypatch):
    today = date(2026, 4, 30)
    sink_calls: list[OrderTicket] = []

    # 1. Decide rebalance
    decision = decide_rebalance(
        latest_signal_date=date(2026, 3, 28), today=today
    )
    assert decision.rebalance is True

    # 2. Build target book from raw aggregated weights — within default
    # exposure limits (per-name <= 25%, gross <= 100%, >= 5 holdings).
    raw_weights = {
        "SPY": 0.20, "TLT": 0.20, "GLD": 0.20, "USO": 0.20, "BIL": 0.20
    }
    book = build_target_book(raw_weights, config=TargetBookConfig(
        max_per_name=0.25, l1_budget=1.0, noise_threshold=0.01,
    ))
    assert sum(abs(v) for v in book.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(abs(v) <= 0.25 for v in book.values())

    # 3. Size positions (1MM NAV, mocked prices)
    nav = 1_000_000.0
    prices = {"SPY": 500.0, "TLT": 100.0, "GLD": 200.0, "USO": 80.0, "BIL": 90.0}
    sized = size_basket(book, prices=prices, nav=nav)
    assert len(sized) == len(book)
    assert all(p.shares != 0 for p in sized)

    # 4. Risk gate
    basket_df = pd.DataFrame([
        {"symbol": p.symbol, "target_weight": book[p.symbol],
         "estimated_trade_dollars": abs(p.notional), "side": "buy"}
        for p in sized
    ])
    rgate = run_pre_trade_risk_checks(basket_df=basket_df, nav=nav)
    assert rgate.ok is True, rgate.report()

    # 5. Build tickets and route through the order router
    tickets = [OrderTicket(p.symbol, p.shares, p.price) for p in sized]
    decisions = submit_orders(
        tickets,
        policy=OrderPolicy(min_notional_usd=50.0, max_notional_usd=10_000_000.0),
        sink=lambda t: sink_calls.append(t) or {"ok": True, "fill_price": t.price},
        log_dir=isolated_runtime["submissions_dir"],
        on_date=today,
    )
    assert all(d.submitted for d in decisions)
    assert len(sink_calls) == len(tickets)

    # 6. Audit trail — every submitted ticket has both pre_submit and
    #    post_submit records on disk (post_submit overwrites pre_submit
    #    at the same idempotency key).
    audit_files = list(isolated_runtime["submissions_dir"].glob("*.json"))
    assert len(audit_files) == len(tickets)
    for f in audit_files:
        record = json.loads(f.read_text())
        assert record["submitted"] is True
        assert record["stage"] == "post_submit"
        assert record["broker_response"]["ok"] is True

    # 7. EOD reconcile: the batch goes into the expected book, and a broker whose
    #    positions show it filled reconciles clean.
    record_submission(
        "paper",
        run_id="20260430_153500",
        positions=pd.DataFrame(columns=POSITION_COLUMNS),  # all cash before this basket
        orders=pd.DataFrame(
            [{"symbol": t.symbol, "action": "BUY" if t.qty > 0 else "SELL", "quantity": abs(t.qty),
              "order_id": i, "perm_id": 100 + i, "status": "Submitted", "estimated_trade_dollars": abs(t.qty) * t.price}
             for i, t in enumerate(tickets, start=1)]
        ),
        execution={"fills_at": "2026-04-30T16:00:00-04:00", "summary": "market-on-close"},
    )  # fmt: skip
    held = {t.symbol: (float(t.qty), t.price) for t in tickets}
    eod = reconcile_end_of_day(
        profile="paper", as_of=today, client=FakeClient(FakeIB(held)),
        now=datetime(2026, 4, 30, 16, 45, tzinfo=clock.NYSE_TZ),
    )  # fmt: skip
    assert eod.ok is True, eod.error
    assert eod.diffs == [] and eod.material_mismatch is False
    assert is_kill_switch_armed() is False


# ─────────────────────────────────────────────────────────────────────────
# Kill-switch path — armed switch blocks the entire batch with audit trail
# ─────────────────────────────────────────────────────────────────────────


def test_full_cycle_with_armed_kill_switch_blocks_everything(isolated_runtime):
    arm_kill_switch("ops review", by="pytest")
    assert is_kill_switch_armed() is True

    sink_calls: list[OrderTicket] = []
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0), OrderTicket("TLT", -5, 100.0)],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
        log_dir=isolated_runtime["submissions_dir"],
        on_date=date(2026, 4, 30),
    )
    assert all(not d.submitted for d in decisions)
    assert all("kill switch" in (d.rejected_reason or "") for d in decisions)
    assert sink_calls == []

    disarm_kill_switch(by="pytest")


# ─────────────────────────────────────────────────────────────────────────
# Idempotency path — replay of the same batch on the same day routes
# only once in total (across two submit_orders calls within the same day,
# the audit-trail directory is the source of truth and the operator is
# expected to re-read it before retrying).
# ─────────────────────────────────────────────────────────────────────────


def test_replay_same_batch_same_day_creates_same_audit_file(isolated_runtime):
    today = date(2026, 4, 30)
    tickets = [OrderTicket("SPY", 10, 400.0)]

    submit_orders(
        tickets,
        sink=lambda t: {"ok": True},
        log_dir=isolated_runtime["submissions_dir"],
        on_date=today,
    )
    files_before = sorted(isolated_runtime["submissions_dir"].glob("*.json"))
    assert len(files_before) == 1

    submit_orders(
        tickets,
        sink=lambda t: {"ok": True},
        log_dir=isolated_runtime["submissions_dir"],
        on_date=today,
    )
    files_after = sorted(isolated_runtime["submissions_dir"].glob("*.json"))
    # Same idempotency key → same file path → no new file created.
    assert files_before == files_after


# ─────────────────────────────────────────────────────────────────────────
# Risk-gate path — empty basket is blocked before any router activity
# ─────────────────────────────────────────────────────────────────────────


def test_empty_basket_is_blocked_at_risk_gate(isolated_runtime):
    rgate = run_pre_trade_risk_checks(basket_df=pd.DataFrame(), nav=100_000.0)
    assert not rgate.ok
    # If we WERE to proceed, the router would also reject the empty
    # ticket list (vacuously: zero decisions, zero side-effects).
    sink_calls: list[OrderTicket] = []
    decisions = submit_orders(
        [],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
        log_dir=isolated_runtime["submissions_dir"],
    )
    assert decisions == []
    assert sink_calls == []
    assert list(isolated_runtime["submissions_dir"].glob("*.json")) == []


# ─────────────────────────────────────────────────────────────────────────
# Material-mismatch path — the EOD reconcile arms the switch when IBKR's
# positions differ from the expected book (a fake broker, no network)
# ─────────────────────────────────────────────────────────────────────────

TODAY = date(2026, 4, 30)
SETTLED = datetime(2026, 4, 30, 16, 45, tzinfo=clock.NYSE_TZ)


def _expect_spy(qty: int, price: float) -> None:
    """A book expecting ``qty`` SPY: all cash, then one buy counted in full."""
    record_submission(
        "paper", run_id="r", positions=pd.DataFrame(columns=POSITION_COLUMNS),
        orders=pd.DataFrame([{"symbol": "SPY", "action": "BUY", "quantity": qty, "order_id": 1, "perm_id": 11,
                              "status": "Submitted", "estimated_trade_dollars": qty * price}]),
        execution={"fills_at": "2026-04-30T16:00:00-04:00", "summary": "market-on-close"},
    )  # fmt: skip


def test_eod_arms_kill_switch_on_material_mismatch(isolated_runtime):  # noqa: ARG001
    _expect_spy(90, 100.0)
    res = reconcile_end_of_day(
        profile="paper", as_of=TODAY, material_threshold_usd=100.0,
        client=FakeClient(FakeIB({"SPY": (100.0, 100.0)})), now=SETTLED,
    )  # fmt: skip
    assert res.ok is True
    assert res.material_mismatch is True
    assert res.diffs[0]["qty_diff"] == 10 and res.diffs[0]["notional_diff_usd"] == 1_000.0
    assert res.report_path is not None
    state = read_kill_switch_state()
    assert state.armed is True
    assert "EOD reconciliation" in state.reason

    disarm_kill_switch(by="cleanup")


def test_eod_does_not_arm_below_material_threshold(isolated_runtime):  # noqa: ARG001
    _expect_spy(99, 50.0)
    res = reconcile_end_of_day(
        profile="paper", as_of=TODAY, material_threshold_usd=100.0,
        client=FakeClient(FakeIB({"SPY": (100.0, 50.0)})), now=SETTLED,
    )  # fmt: skip
    assert res.diffs[0]["notional_diff_usd"] == 50.0
    assert res.material_mismatch is False
    assert is_kill_switch_armed() is False


def test_eod_reports_an_unreadable_book_as_a_failure(isolated_runtime):
    """A corrupt expected book is not a clean reconcile: the result says so, names
    the file, and the broker is never asked."""
    from src.runtime.end_of_day_reconcile import book_path

    path = book_path("paper")
    path.write_text("{not json")
    client = FakeClient(FakeIB({"SPY": (100.0, 50.0)}))
    res = reconcile_end_of_day(profile="paper", as_of=TODAY, client=client, now=SETTLED)
    assert res.ok is False and res.material_mismatch is False
    assert path.name in res.error and client.connections == 0
    assert is_kill_switch_armed() is False
