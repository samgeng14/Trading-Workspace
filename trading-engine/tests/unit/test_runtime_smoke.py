"""Smoke tests for the kill switch and order routing."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.execution.kill_switch import (  # noqa: E402
    KillSwitchTripped,
    arm_kill_switch,
    disarm_kill_switch,
    is_kill_switch_armed,
    require_kill_switch_clear,
)
from src.execution.order_policy import OrderTicket  # noqa: E402
from src.execution.order_router import idempotency_key, submit_orders  # noqa: E402


# ── kill switch ────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr("src.execution.kill_switch.KILL_SWITCH_PATH",
                        tmp_path / "kill_switch.json")
    yield
    # cleanup is automatic via tmp_path


def test_kill_switch_default_is_disarmed(isolated_kill_switch):
    assert is_kill_switch_armed() is False
    require_kill_switch_clear()  # does not raise


def test_kill_switch_arm_blocks_clear_call(isolated_kill_switch):
    arm_kill_switch("test reason", by="pytest")
    assert is_kill_switch_armed() is True
    with pytest.raises(KillSwitchTripped):
        require_kill_switch_clear()
    disarm_kill_switch(by="pytest")
    assert is_kill_switch_armed() is False


# ── order router idempotency ───────────────────────────────────────────────


def test_idempotency_key_stable_for_same_inputs():
    t = OrderTicket("SPY", 10, 400.0)
    from datetime import date
    k1 = idempotency_key(t, on_date=date(2026, 4, 30))
    k2 = idempotency_key(t, on_date=date(2026, 4, 30))
    assert k1 == k2


def test_idempotency_key_differs_across_days():
    t = OrderTicket("SPY", 10, 400.0)
    from datetime import date
    assert idempotency_key(t, on_date=date(2026, 4, 30)) != \
           idempotency_key(t, on_date=date(2026, 5, 1))


def test_submit_orders_with_armed_switch_rejects_all(isolated_kill_switch):
    arm_kill_switch("test", by="pytest")
    sink_calls = []
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0), OrderTicket("TLT", -5, 100.0)],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
    )
    assert all(not d.submitted for d in decisions)
    assert sink_calls == []  # sink never invoked
    disarm_kill_switch(by="pytest")


def test_submit_orders_dry_run_skips_sink(isolated_kill_switch, tmp_path, monkeypatch):
    monkeypatch.setattr("src.execution.order_router.SUBMISSION_LOG_DIR", tmp_path)
    sink_calls = []
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0)],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
        dry_run=True,
    )
    assert sink_calls == []
    assert all(not d.submitted for d in decisions)
    assert all(d.rejected_reason == "dry_run" for d in decisions)


def test_submit_orders_routes_clean_ticket(isolated_kill_switch, tmp_path, monkeypatch):
    monkeypatch.setattr("src.execution.order_router.SUBMISSION_LOG_DIR", tmp_path)
    received = []
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0)],
        sink=lambda t: received.append(t) or {"ok": True, "broker_order_id": 42},
    )
    assert len(received) == 1
    assert all(d.submitted for d in decisions)
    assert decisions[0].broker_response.get("ok") is True
