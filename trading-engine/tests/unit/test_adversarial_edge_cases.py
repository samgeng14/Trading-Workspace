"""Adversarial edge-case tests for hardened modules.

Each test pins down a failure mode that we explicitly defend against
in code. The point isn't to test happy paths (the regular unit suite
does that) — it's to make sure that future drift doesn't accidentally
re-open a bug we already fixed.

Categories:
  1. Kill-switch corruption (atomic write, fail-closed)
  2. Order-router idempotency-key collisions, audit-log failures
  3. Sizing overflow + invalid input handling
  4. TargetBookConfig validation + clip math edge cases
  5. OrderPolicy validation + side/qty consistency
  6. Partial-fill defensive paths
  7. Hedging-overlay sanitisation + macro-signal NaN
  8. Auto-rebalance: clock-skew, future-date, type guards
  9. validate_weight_frame: duplicate columns, all-null, empty
 10. End-of-day reconcile: input validation, idempotent arm
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
for p in (str(PROJECT_ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.execution.kill_switch import (  # noqa: E402
    KillSwitchCorrupted,
    KillSwitchTripped,
    arm_kill_switch,
    disarm_kill_switch,
    is_kill_switch_armed,
    read_kill_switch_state,
    require_kill_switch_clear,
)
from src.execution.order_policy import OrderPolicy, OrderTicket  # noqa: E402
from src.execution.order_router import (  # noqa: E402
    idempotency_key,
    submit_orders,
)
from src.execution.partial_fill_handler import Action, decide_follow_up  # noqa: E402
from src.portfolio.hedging_overlay import (  # noqa: E402
    HedgeOverlayConfig,
    apply_overlay,
)
from src.portfolio.sizing import size_position  # noqa: E402
from src.portfolio.target_builder import (  # noqa: E402
    TargetBookConfig,
    build_target_book,
)
from src.runtime.auto_rebalance import (  # noqa: E402
    DriftDecision,
    decide_rebalance,
    decide_rebalance_with_drift,
    is_month_end_window,
)
from src.runtime.end_of_day_reconcile import reconcile_end_of_day  # noqa: E402
from trading_engine.models.catalogue.base import validate_weight_frame  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# 1. Kill-switch corruption
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_kill_switch(tmp_path, monkeypatch):
    p = tmp_path / "kill_switch.json"
    monkeypatch.setattr("src.execution.kill_switch.KILL_SWITCH_PATH", p)
    yield p


def test_kill_switch_corrupted_json_raises(isolated_kill_switch):
    isolated_kill_switch.write_text("{not json", encoding="utf-8")
    with pytest.raises(KillSwitchCorrupted):
        read_kill_switch_state()


def test_kill_switch_corrupted_root_not_dict_raises(isolated_kill_switch):
    isolated_kill_switch.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(KillSwitchCorrupted):
        read_kill_switch_state()


def test_kill_switch_corrupted_fails_closed_in_is_armed(isolated_kill_switch):
    # is_kill_switch_armed must report ARMED when the file is corrupt.
    isolated_kill_switch.write_text("garbage", encoding="utf-8")
    assert is_kill_switch_armed() is True


def test_kill_switch_corrupted_blocks_require_clear(isolated_kill_switch):
    isolated_kill_switch.write_text("garbage", encoding="utf-8")
    with pytest.raises(KillSwitchTripped):
        require_kill_switch_clear()


def test_kill_switch_atomic_write_no_temp_files_left(isolated_kill_switch):
    arm_kill_switch("test", by="pytest")
    disarm_kill_switch(by="pytest")
    arm_kill_switch("test 2", by="pytest")
    # No leftover .tmp files in the directory after a few writes.
    leftovers = [p for p in isolated_kill_switch.parent.iterdir()
                 if p.suffix == ".tmp" or ".tmp" in p.name]
    assert leftovers == []
    disarm_kill_switch(by="pytest")


def test_kill_switch_arm_requires_nonempty_reason(isolated_kill_switch):
    with pytest.raises(ValueError):
        arm_kill_switch("", by="pytest")
    with pytest.raises(ValueError):
        arm_kill_switch("   ", by="pytest")


def test_kill_switch_missing_file_is_disarmed(isolated_kill_switch):
    # Default state for missing file. require_clear must NOT raise.
    if isolated_kill_switch.exists():
        isolated_kill_switch.unlink()
    require_kill_switch_clear()  # no exception
    assert is_kill_switch_armed() is False


# ─────────────────────────────────────────────────────────────────────────
# 2. Order-router idempotency + audit-log resilience
# ─────────────────────────────────────────────────────────────────────────


def test_idempotency_key_rejects_empty_symbol():
    with pytest.raises(ValueError):
        idempotency_key(OrderTicket("", 10, 100.0))


def test_idempotency_key_rejects_non_int_qty():
    with pytest.raises(TypeError):
        idempotency_key(OrderTicket("SPY", 10.5, 100.0))  # type: ignore[arg-type]


def test_router_detects_within_batch_duplicate(isolated_kill_switch, tmp_path):
    sink_calls = []
    decisions = submit_orders(
        [
            OrderTicket("SPY", 10, 400.0),
            OrderTicket("SPY", 10, 400.0),  # duplicate (same key)
        ],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
        on_date=date(2026, 4, 30),
        log_dir=tmp_path,
    )
    assert len(decisions) == 2
    submitted = [d for d in decisions if d.submitted]
    rejected = [d for d in decisions if not d.submitted]
    assert len(submitted) == 1
    assert len(rejected) == 1
    assert "duplicate" in (rejected[0].rejected_reason or "")
    # Sink should only be called once.
    assert len(sink_calls) == 1


def test_router_dry_run_persists_audit_record(isolated_kill_switch, tmp_path):
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0)],
        sink=lambda t: {"ok": True},
        log_dir=tmp_path,
        dry_run=True,
    )
    assert all(not d.submitted for d in decisions)
    # An audit record exists for the dry-run.
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["reason"] == "dry_run"


def test_router_audit_log_failure_does_not_block_submission(
    isolated_kill_switch, tmp_path, monkeypatch
):
    """If the audit log can't be written, orders STILL flow to broker —
    the audit failure is logged but doesn't cancel the trade."""
    # Redirect log dir to an unwritable path (a file masquerading as a dir).
    blocker = tmp_path / "blocker"
    blocker.write_text("I am not a directory", encoding="utf-8")
    sink_calls = []
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0)],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
        log_dir=blocker,  # passing a file as a dir → write fails
    )
    assert len(sink_calls) == 1  # sink WAS called
    assert decisions[0].submitted is True


def test_router_handles_non_dict_broker_response(isolated_kill_switch, tmp_path):
    decisions = submit_orders(
        [OrderTicket("SPY", 10, 400.0)],
        sink=lambda t: "weird string response",
        log_dir=tmp_path,
    )
    # Router coerces non-dict into a structured error dict.
    assert isinstance(decisions[0].broker_response, dict)
    assert decisions[0].broker_response["ok"] is False


def test_router_invalid_ticket_does_not_poison_batch(isolated_kill_switch, tmp_path):
    """A malformed ticket fails its own row but the batch keeps going."""
    sink_calls = []
    decisions = submit_orders(
        [
            OrderTicket("", 10, 400.0),     # invalid (empty symbol)
            OrderTicket("SPY", 10, 400.0),  # valid
        ],
        sink=lambda t: sink_calls.append(t) or {"ok": True},
        log_dir=tmp_path,
    )
    assert len(decisions) == 2
    assert decisions[0].submitted is False
    assert "invalid ticket" in (decisions[0].rejected_reason or "")
    assert decisions[1].submitted is True
    assert len(sink_calls) == 1


def test_router_iterator_input_works(isolated_kill_switch, tmp_path):
    """Caller can pass a one-shot iterator; we materialise internally."""
    def gen():
        yield OrderTicket("SPY", 10, 400.0)
        yield OrderTicket("TLT", -5, 100.0)

    decisions = submit_orders(gen(), sink=lambda t: {"ok": True}, log_dir=tmp_path)
    assert len(decisions) == 2


# ─────────────────────────────────────────────────────────────────────────
# 3. Sizing overflow + invalid input
# ─────────────────────────────────────────────────────────────────────────


def test_sizing_extreme_values_returns_zero():
    # NAV * weight = 1e308 * 1.0; / 1e-308 = 1e616 → inf.
    p = size_position("X", weight=1.0, nav=1e308, price=1e-308)
    assert p.shares == 0
    assert p.notional == 0.0


def test_sizing_zero_lot_returns_zero():
    p = size_position("X", weight=0.1, nav=10_000.0, price=100.0, round_lots=0)
    assert p.shares == 0


def test_sizing_negative_contract_multiplier_returns_zero():
    p = size_position("X", weight=0.1, nav=10_000.0, price=100.0, contract_multiplier=-1)
    assert p.shares == 0


def test_sizing_empty_symbol_returns_blank():
    p = size_position("", weight=0.1, nav=10_000.0, price=100.0)
    assert p.symbol == "" and p.shares == 0


def test_sizing_inf_inputs_safe():
    p = size_position("X", weight=float("inf"), nav=10_000.0, price=100.0)
    assert p.shares == 0


# ─────────────────────────────────────────────────────────────────────────
# 4. TargetBookConfig + build_target_book edge cases
# ─────────────────────────────────────────────────────────────────────────


def test_target_book_config_rejects_invalid_max_per_name():
    with pytest.raises(ValueError):
        TargetBookConfig(max_per_name=0.0)
    with pytest.raises(ValueError):
        TargetBookConfig(max_per_name=-0.1)
    with pytest.raises(ValueError):
        TargetBookConfig(max_per_name=float("nan"))


def test_target_book_config_rejects_invalid_l1_budget():
    with pytest.raises(ValueError):
        TargetBookConfig(l1_budget=0.0)
    with pytest.raises(ValueError):
        TargetBookConfig(l1_budget=float("inf"))


def test_target_book_config_rejects_negative_noise_threshold():
    with pytest.raises(ValueError):
        TargetBookConfig(noise_threshold=-0.001)


def test_target_book_handles_zero_weights():
    out = build_target_book({"A": 0.0, "B": 0.0})
    assert out == {}


def test_target_book_handles_non_string_keys():
    # Non-string keys are silently dropped (defensive sanitiser).
    out = build_target_book({"A": 0.5, 42: 0.3, "": 0.2, "B": 0.1})  # type: ignore[dict-item]
    assert "A" in out and "B" in out
    assert 42 not in out and "" not in out


def test_target_book_negative_weights_clipped_correctly():
    """copysign edge case: -0.0 should map to 0, not get a phantom sign."""
    out = build_target_book(
        {"A": -0.99, "B": 0.99},
        config=TargetBookConfig(max_per_name=0.5, l1_budget=2.0),
    )
    assert math.isclose(out["A"], -0.5, abs_tol=1e-9)
    assert math.isclose(out["B"], 0.5, abs_tol=1e-9)


def test_target_book_rejects_mixed_garbage_types():
    out = build_target_book({"A": "notanumber", "B": 0.3, "C": None})  # type: ignore[dict-item]
    assert "A" not in out and "C" not in out
    assert "B" in out


# ─────────────────────────────────────────────────────────────────────────
# 5. OrderPolicy validation
# ─────────────────────────────────────────────────────────────────────────


def test_policy_rejects_negative_min_notional():
    with pytest.raises(ValueError):
        OrderPolicy(min_notional_usd=-10.0)


def test_policy_rejects_min_above_max():
    with pytest.raises(ValueError):
        OrderPolicy(min_notional_usd=100.0, max_notional_usd=50.0)


def test_policy_rejects_nonpositive_max_notional():
    with pytest.raises(ValueError):
        OrderPolicy(max_notional_usd=0.0)


def test_policy_rejects_invalid_allowlist_entries():
    with pytest.raises(ValueError):
        OrderPolicy(allowlist=("SPY", ""))
    with pytest.raises(ValueError):
        OrderPolicy(allowlist=("SPY", 42))  # type: ignore[arg-type]


def test_policy_evaluates_unknown_side():
    policy = OrderPolicy()
    d = policy.evaluate(OrderTicket("SPY", 10, 400.0, side="up"))
    assert not d.accept and "unknown side" in d.reason


def test_policy_rejects_inconsistent_buy_side():
    policy = OrderPolicy()
    d = policy.evaluate(OrderTicket("SPY", -5, 400.0, side="buy"))
    assert not d.accept and "buy" in d.reason.lower()


def test_policy_rejects_inconsistent_sell_side():
    policy = OrderPolicy()
    d = policy.evaluate(OrderTicket("SPY", +5, 400.0, side="sell"))
    assert not d.accept and "sell" in d.reason.lower()


# ─────────────────────────────────────────────────────────────────────────
# 6. Partial-fill defensive paths
# ─────────────────────────────────────────────────────────────────────────


def test_partial_fill_negative_filled_cancels():
    d = decide_follow_up(side="buy", qty_total=10, qty_filled=-1, last_price=100.0)
    assert d.action == Action.CANCEL_ONLY


def test_partial_fill_overfilled_lets_rest():
    d = decide_follow_up(side="buy", qty_total=10, qty_filled=15, last_price=100.0)
    assert d.action == Action.LET_REST_WORK


def test_partial_fill_nan_price_cancels():
    d = decide_follow_up(side="buy", qty_total=10, qty_filled=2,
                         last_price=float("nan"), cancel_below_pct=0.5)
    assert d.action == Action.CANCEL_ONLY


def test_partial_fill_nonpositive_tick_cancels():
    d = decide_follow_up(side="buy", qty_total=10, qty_filled=2,
                         last_price=100.0, tick=0.0, cancel_below_pct=0.5)
    assert d.action == Action.CANCEL_ONLY


def test_partial_fill_unknown_side_cancels():
    d = decide_follow_up(side="up", qty_total=10, qty_filled=2,
                         last_price=100.0, cancel_below_pct=0.5)
    assert d.action == Action.CANCEL_ONLY


def test_partial_fill_pct_out_of_range_cancels():
    d = decide_follow_up(side="buy", qty_total=10, qty_filled=2,
                         last_price=100.0, cancel_below_pct=1.5)
    assert d.action == Action.CANCEL_ONLY


# ─────────────────────────────────────────────────────────────────────────
# 7. Hedging-overlay sanitisation
# ─────────────────────────────────────────────────────────────────────────


def test_overlay_config_rejects_zero_hedge_weight():
    with pytest.raises(ValueError):
        HedgeOverlayConfig(enabled=True, hedge_weight=0.0)


def test_overlay_config_rejects_too_large_hedge_weight():
    with pytest.raises(ValueError):
        HedgeOverlayConfig(enabled=True, hedge_weight=10.0)


def test_overlay_drops_nan_weights_from_input():
    cfg = HedgeOverlayConfig(enabled=False)
    out = apply_overlay(
        {"A": 0.5, "B": float("nan"), "C": float("inf"), "D": 0.3},
        config=cfg,
    )
    assert "A" in out and "D" in out
    assert "B" not in out and "C" not in out


def test_overlay_handles_nan_macro_signal():
    cfg = HedgeOverlayConfig(enabled=True, hedge_weight=0.1, trigger_signal_threshold=-0.5)
    out = apply_overlay({"SPY": 0.5}, macro_signal=float("nan"), config=cfg)
    # NaN comparisons are false → no trigger.
    assert "TLT-US" not in out


def test_overlay_handles_non_numeric_macro_signal():
    cfg = HedgeOverlayConfig(enabled=True, trigger_signal_threshold=-0.5)
    out = apply_overlay({"SPY": 0.5}, macro_signal="risk-off", config=cfg)  # type: ignore[arg-type]
    # Non-coercible signal → no trigger.
    assert "TLT-US" not in out


# ─────────────────────────────────────────────────────────────────────────
# 8. Auto-rebalance defensive paths
# ─────────────────────────────────────────────────────────────────────────


def test_decide_rebalance_future_signal_triggers():
    d = decide_rebalance(latest_signal_date=date(2026, 6, 1), today=date(2026, 5, 1))
    assert d.rebalance is True
    assert "AFTER" in d.reason or "anomaly" in d.reason


def test_decide_rebalance_rejects_non_date_today():
    with pytest.raises(TypeError):
        decide_rebalance(latest_signal_date=None, today="2026-04-30")  # type: ignore[arg-type]


def test_decide_rebalance_rejects_non_date_signal():
    with pytest.raises(TypeError):
        decide_rebalance(latest_signal_date="2026-04-01",  # type: ignore[arg-type]
                         today=date(2026, 4, 30))


def test_is_month_end_window_rejects_zero_days():
    with pytest.raises(ValueError):
        is_month_end_window(date(2026, 4, 30), days=0)


def test_is_month_end_window_rejects_negative_days():
    with pytest.raises(ValueError):
        is_month_end_window(date(2026, 4, 30), days=-1)


def test_is_month_end_window_large_days():
    # days=31 → every day in any month qualifies
    assert is_month_end_window(date(2026, 4, 1), days=31) is True


def test_is_month_end_window_february_leap_year():
    assert is_month_end_window(date(2024, 2, 29)) is True
    assert is_month_end_window(date(2024, 2, 24)) is False
    assert is_month_end_window(date(2024, 2, 25)) is True  # last 5 days


# ─────────────────────────────────────────────────────────────────────────
# 8b. Drift-band rebalance gate
# ─────────────────────────────────────────────────────────────────────────
#
# Pins the daily drift cadence rules so that a refactor cannot silently
# revert to the legacy monthly logic, which would mean no trades fire
# until the calendar month flips. Each test names the exact trigger
# (composition / drift / cooldown / cold start) it pins down.


_SAMPLE_BASKET = {"SPY": 0.20, "TLT": 0.15, "GLD": 0.10, "BIL": 0.05}


def test_drift_force_flag_overrides_everything():
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=_SAMPLE_BASKET,  # zero drift
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 5),  # same-day cooldown active
        last_basket=_SAMPLE_BASKET,
        cooldown_days=10,
        force=True,
    )
    assert d.rebalance is True
    assert "force" in d.reason.lower()


def test_drift_cold_start_with_no_prior_basket_fires():
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights={},
        today=date(2026, 5, 5),
        last_basket=None,
    )
    assert d.rebalance is True
    assert "cold start" in d.reason.lower()


def test_drift_below_band_with_unchanged_composition_does_not_fire():
    # SPY drifted 21 → 22.9%, well under 5% band.
    current = dict(_SAMPLE_BASKET)
    current["SPY"] = 0.229
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=0,
    )
    assert d.rebalance is False
    assert d.max_drift == pytest.approx(0.029)


def test_drift_strictly_above_band_fires():
    # SPY drifted 20 → 26%, max drift 0.06 strictly above 0.05 band.
    current = dict(_SAMPLE_BASKET)
    current["SPY"] = 0.26
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=0,
    )
    assert d.rebalance is True
    assert d.max_drift == pytest.approx(0.06)
    assert "drift" in d.reason.lower()


def test_drift_exact_band_boundary_does_not_fire():
    # Strict-inequality contract: max_drift == band → skip.
    # Pins down that "5% band" means "trade when > 5%", not "≥ 5%".
    current = dict(_SAMPLE_BASKET)
    current["SPY"] = 0.25  # drift = 0.05 exact
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=0,
    )
    assert d.rebalance is False
    assert d.max_drift == pytest.approx(0.05)


def test_drift_composition_change_fires_independent_of_drift_band():
    # Same drift magnitude as the "below band" case, but a new name with
    # material weight has entered the basket → composition triggers.
    new_target = {"SPY": 0.20, "TLT": 0.15, "GLD": 0.10, "BIL": 0.05, "INDA": 0.08}
    d = decide_rebalance_with_drift(
        target_weights=new_target,
        current_weights=_SAMPLE_BASKET,  # missing INDA → drift 0.08 anyway,
                                         # but composition trigger should win
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=_SAMPLE_BASKET,
        composition_threshold=0.05,
        drift_threshold=0.50,  # set absurdly high so only composition could fire
        cooldown_days=0,
    )
    assert d.rebalance is True
    assert d.composition_changed is True
    assert "composition" in d.reason.lower()


def test_drift_composition_threshold_ignores_immaterial_changes():
    # A new name appears at 1% — below the 5% materiality cutoff — so
    # composition should NOT be flagged as changed.
    new_target = dict(_SAMPLE_BASKET)
    new_target["VIXY"] = 0.01
    d = decide_rebalance_with_drift(
        target_weights=new_target,
        current_weights=_SAMPLE_BASKET,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=_SAMPLE_BASKET,
        composition_threshold=0.05,
        drift_threshold=0.10,  # well above the 1% drift this introduces
        cooldown_days=0,
    )
    assert d.rebalance is False
    assert d.composition_changed is False


def test_drift_cooldown_blocks_drift_trigger():
    current = dict(_SAMPLE_BASKET)
    current["SPY"] = 0.30  # drift 0.10, well over 0.05 band
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 4),  # 1 day ago
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=3,
    )
    assert d.rebalance is False
    assert d.in_cooldown is True
    assert d.cooldown_remaining_days == 2
    assert "cooldown" in d.reason.lower()


def test_drift_cooldown_blocks_composition_trigger_too():
    new_target = {"SPY": 0.20, "TLT": 0.15, "GLD": 0.10, "BIL": 0.05, "INDA": 0.08}
    d = decide_rebalance_with_drift(
        target_weights=new_target,
        current_weights=_SAMPLE_BASKET,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 5),
        last_basket=_SAMPLE_BASKET,
        composition_threshold=0.05,
        drift_threshold=0.05,
        cooldown_days=3,
    )
    assert d.rebalance is False
    assert d.composition_changed is True
    assert d.in_cooldown is True
    assert "cooldown" in d.reason.lower()


def test_drift_cooldown_does_not_block_cold_start():
    # A cold start must always fire, even if last_submitted_at says
    # we're inside a cooldown — a missing last_basket means we have
    # nothing to be cooled-down vs.
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights={},
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 5),
        last_basket=None,
        cooldown_days=99,
    )
    assert d.rebalance is True
    assert "cold start" in d.reason.lower()


def test_drift_cooldown_zero_days_never_blocks():
    current = dict(_SAMPLE_BASKET)
    current["SPY"] = 0.30
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 5),  # same day
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=0,
    )
    assert d.rebalance is True
    assert d.in_cooldown is False


def test_drift_future_last_submitted_clock_skew_does_not_apply_cooldown():
    # If the recorded last submission is somehow AFTER today, treat it
    # as not-in-cooldown rather than blocking forever (or worse,
    # silently treating a negative duration as small).
    current = dict(_SAMPLE_BASKET)
    current["SPY"] = 0.30  # drift 0.10
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 20),  # 15 days in the future
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=3,
    )
    assert d.rebalance is True
    assert d.in_cooldown is False


def test_drift_symbol_only_in_target_counts_full_weight_as_drift():
    # New symbol at 8% target with nothing currently held → drift 0.08.
    target = dict(_SAMPLE_BASKET)
    target["INDA"] = 0.08
    d = decide_rebalance_with_drift(
        target_weights=target,
        current_weights=_SAMPLE_BASKET,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=target,  # composition unchanged so we isolate drift
        drift_threshold=0.05,
        cooldown_days=0,
    )
    assert d.max_drift == pytest.approx(0.08)
    assert d.rebalance is True


def test_drift_symbol_only_in_current_counts_full_weight_as_drift():
    # We're holding something the new target doesn't include — drift
    # equals the held weight, and that should be enough to fire.
    current = dict(_SAMPLE_BASKET)
    current["VIXY"] = 0.07
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=current,
        today=date(2026, 5, 5),
        last_submitted_at=date(2026, 5, 1),
        last_basket=_SAMPLE_BASKET,
        drift_threshold=0.05,
        cooldown_days=0,
    )
    assert d.max_drift == pytest.approx(0.07)
    assert d.rebalance is True


def test_drift_rejects_non_date_today():
    with pytest.raises(TypeError):
        decide_rebalance_with_drift(
            target_weights=_SAMPLE_BASKET,
            current_weights=_SAMPLE_BASKET,
            today="2026-05-05",  # type: ignore[arg-type]
        )


def test_drift_rejects_non_date_last_submitted():
    with pytest.raises(TypeError):
        decide_rebalance_with_drift(
            target_weights=_SAMPLE_BASKET,
            current_weights=_SAMPLE_BASKET,
            today=date(2026, 5, 5),
            last_submitted_at="2026-05-04",  # type: ignore[arg-type]
        )


def test_drift_rejects_negative_thresholds():
    with pytest.raises(ValueError):
        decide_rebalance_with_drift(
            target_weights=_SAMPLE_BASKET,
            current_weights=_SAMPLE_BASKET,
            today=date(2026, 5, 5),
            drift_threshold=-0.01,
        )
    with pytest.raises(ValueError):
        decide_rebalance_with_drift(
            target_weights=_SAMPLE_BASKET,
            current_weights=_SAMPLE_BASKET,
            today=date(2026, 5, 5),
            cooldown_days=-1,
        )


def test_drift_decision_dataclass_is_frozen():
    # The decision is logged downstream — mutating it post-hoc would be
    # a footgun. Pin the frozen contract.
    d = decide_rebalance_with_drift(
        target_weights=_SAMPLE_BASKET,
        current_weights=_SAMPLE_BASKET,
        today=date(2026, 5, 5),
        last_basket=_SAMPLE_BASKET,
    )
    assert isinstance(d, DriftDecision)
    with pytest.raises((AttributeError, TypeError)):  # frozen dataclass
        d.rebalance = True  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────
# 9. validate_weight_frame edge cases
# ─────────────────────────────────────────────────────────────────────────


def _wf(rows):
    return pl.DataFrame(rows)


def test_validate_weight_frame_empty_frame_with_required_columns_passes():
    df = pl.DataFrame({"date": [], "A": []}, schema={"date": pl.Date, "A": pl.Float64})
    validate_weight_frame(df, ["A"])  # passes — no rows to violate bounds


def test_validate_weight_frame_require_nonempty_fails_on_empty():
    df = pl.DataFrame({"date": [], "A": []}, schema={"date": pl.Date, "A": pl.Float64})
    with pytest.raises(ValueError):
        validate_weight_frame(df, ["A"], require_nonempty=True)


def test_validate_weight_frame_rejects_wrong_dtype():
    df = pl.DataFrame({"date": [date(2026, 1, 1)], "A": [1]})  # A is Int64
    with pytest.raises(ValueError):
        validate_weight_frame(df, ["A"])


def test_validate_weight_frame_rejects_nan():
    df = pl.DataFrame({"date": [date(2026, 1, 1)], "A": [float("nan")]})
    with pytest.raises(ValueError):
        validate_weight_frame(df, ["A"])


def test_validate_weight_frame_rejects_out_of_bounds():
    df = pl.DataFrame({"date": [date(2026, 1, 1)], "A": [10.0]})
    with pytest.raises(ValueError):
        validate_weight_frame(df, ["A"], weight_bound=1.0)


def test_validate_weight_frame_long_only_rejects_negative():
    df = pl.DataFrame({"date": [date(2026, 1, 1)], "A": [-0.1]})
    with pytest.raises(ValueError):
        validate_weight_frame(df, ["A"], allow_short=False)


def test_validate_weight_frame_rejects_invalid_weight_bound():
    df = pl.DataFrame({"date": [date(2026, 1, 1)], "A": [0.5]})
    with pytest.raises(ValueError):
        validate_weight_frame(df, ["A"], weight_bound=0)


def test_validate_weight_frame_rejects_empty_ticker_in_list():
    df = pl.DataFrame({"date": [date(2026, 1, 1)], "A": [0.5]})
    with pytest.raises(ValueError):
        validate_weight_frame(df, [""])


# ─────────────────────────────────────────────────────────────────────────
# 10. End-of-day reconcile validation
# ─────────────────────────────────────────────────────────────────────────


def test_reconcile_rejects_empty_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("src.runtime.end_of_day_reconcile.RECON_DIR", tmp_path)
    with pytest.raises(ValueError):
        reconcile_end_of_day(profile="")


def test_reconcile_rejects_nan_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("src.runtime.end_of_day_reconcile.RECON_DIR", tmp_path)
    with pytest.raises(ValueError):
        reconcile_end_of_day(profile="paper", material_threshold_usd=float("nan"))


def test_reconcile_rejects_negative_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("src.runtime.end_of_day_reconcile.RECON_DIR", tmp_path)
    with pytest.raises(ValueError):
        reconcile_end_of_day(profile="paper", material_threshold_usd=-1.0)


def test_reconcile_rerun_does_not_double_arm(
    tmp_path, monkeypatch, isolated_kill_switch
):
    """Re-running EOD reconcile when the switch is already armed must
    not re-arm (which would overwrite the original armed_at + reason)."""
    monkeypatch.setattr("src.runtime.end_of_day_reconcile.RECON_DIR", tmp_path)
    arm_kill_switch("first arm reason", by="initial")
    state_before = read_kill_switch_state()

    # A material mismatch on the rerun must hit the idempotent check.
    monkeypatch.setattr(
        "src.broker.ibkr.reconciler.diff_book_vs_account",
        lambda **_: [{"symbol": "SPY", "notional_diff_usd": 50_000.0}],
    )
    res = reconcile_end_of_day(profile="paper", as_of=date(2026, 4, 30))
    assert res.ok is True
    assert res.material_mismatch is True

    state_after = read_kill_switch_state()
    # Original arm metadata preserved.
    assert state_after.reason == state_before.reason
    assert state_after.armed_at == state_before.armed_at
    assert state_after.last_change_by == state_before.last_change_by

    disarm_kill_switch(by="cleanup")
