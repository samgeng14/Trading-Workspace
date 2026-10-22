"""Unit tests for end-of-day reconciliation + auto-rebalance decision."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.auto_rebalance import (  # noqa: E402
    decide_rebalance,
    is_month_end_window,
)
from src.runtime.end_of_day_reconcile import reconcile_end_of_day  # noqa: E402


# ── auto_rebalance ─────────────────────────────────────────────────────────


def test_force_flag_always_rebalances():
    d = decide_rebalance(latest_signal_date=date(2026, 4, 1), today=date(2026, 4, 30), force=True)
    assert d.rebalance is True
    assert "force" in d.reason.lower()


def test_no_signal_means_rebalance():
    d = decide_rebalance(latest_signal_date=None, today=date(2026, 4, 30))
    assert d.rebalance is True


def test_signal_in_prior_month_triggers_rebalance():
    d = decide_rebalance(latest_signal_date=date(2026, 3, 28), today=date(2026, 4, 5))
    assert d.rebalance is True


def test_signal_in_current_month_skips():
    d = decide_rebalance(latest_signal_date=date(2026, 4, 1), today=date(2026, 4, 30))
    assert d.rebalance is False


def test_month_end_window():
    assert is_month_end_window(date(2026, 4, 30)) is True
    assert is_month_end_window(date(2026, 4, 26)) is True
    assert is_month_end_window(date(2026, 4, 1)) is False


# ── end_of_day_reconcile ───────────────────────────────────────────────────


def test_reconcile_reports_unreachable_broker_as_failure(tmp_path, monkeypatch):
    # An unreachable gateway proves nothing about the book, so it must not
    # come back as a clean (ok, no-diff) reconcile.
    import pandas as pd

    from src.broker.ibkr import reconciler
    from src.production.runtime.build_paper_basket import POSITION_COLUMNS
    from src.runtime.end_of_day_reconcile import record_submission

    class _DownClient:
        config = SimpleNamespace(host="127.0.0.1", port=4002, account=None)

        def connect(self):
            raise ConnectionRefusedError("gateway down")

    monkeypatch.setattr("src.runtime.end_of_day_reconcile.RECON_DIR", tmp_path)
    monkeypatch.setattr(reconciler, "_make_readonly_client", lambda profile: _DownClient())
    record_submission(  # something to reconcile, with its auction behind us
        "paper", run_id="r", positions=pd.DataFrame(columns=POSITION_COLUMNS),
        orders=pd.DataFrame([{"symbol": "SPY", "action": "BUY", "quantity": 1, "order_id": 1, "perm_id": 1,
                              "status": "Submitted", "estimated_trade_dollars": 100.0}]),
        execution={"fills_at": "2026-04-29T16:00:00-04:00"},
    )  # fmt: skip
    res = reconcile_end_of_day(profile="paper", as_of=date(2026, 4, 30))
    assert res.ok is False
    assert res.material_mismatch is False
    assert "IBKR connect failed" in (res.error or "")
