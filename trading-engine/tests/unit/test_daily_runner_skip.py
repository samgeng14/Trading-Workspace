"""``--skip-if-ran-today`` must only honour real (non-dry) successful runs, and a
real run skips the days NYSE is closed."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common import clock  # noqa: E402
from src.production import daily_runner  # noqa: E402


def _write_run(run_dir: Path, stamp: str, **doc) -> Path:
    path = run_dir / f"daily_run_{stamp}.json"
    path.write_text(json.dumps(doc))
    return path


def _setup(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 10, 9))
    run_dir = tmp_path / "artifacts" / "runs"
    run_dir.mkdir(parents=True)
    return run_dir


def test_dry_run_does_not_count_as_ran_today(tmp_path, monkeypatch):
    run_dir = _setup(tmp_path, monkeypatch)
    _write_run(run_dir, "20261009_101500", ok=True, dry_run=True)
    assert daily_runner._already_ran_today_successfully() is None


def test_real_successful_run_counts(tmp_path, monkeypatch):
    run_dir = _setup(tmp_path, monkeypatch)
    _write_run(run_dir, "20261009_101500", ok=True, dry_run=True)
    real = _write_run(run_dir, "20261009_163200", ok=True, dry_run=False)
    assert daily_runner._already_ran_today_successfully() == real


def test_failed_run_does_not_count(tmp_path, monkeypatch):
    run_dir = _setup(tmp_path, monkeypatch)
    _write_run(run_dir, "20261009_163200", ok=False, dry_run=False)
    assert daily_runner._already_ran_today_successfully() is None


# ── Days NYSE is closed ───────────────────────────────────────────────────


def _stop_at_append(monkeypatch) -> list[str]:
    """Stop a run at its first step, so a test sees whether the closed-day gate let it in."""
    calls: list[str] = []
    monkeypatch.setattr(
        daily_runner, "step_append_daily", lambda **_: calls.append("append") or {"ok": False, "error": "stub"}
    )
    return calls


def test_a_market_holiday_skips_a_real_run(tmp_path, monkeypatch):
    run_dir = _setup(tmp_path, monkeypatch)
    thanksgiving = date(2026, 11, 26)
    assert not clock.is_us_business_day(thanksgiving)
    monkeypatch.setattr(daily_runner, "_today", lambda: thanksgiving)
    monkeypatch.setattr(daily_runner, "datetime", SimpleNamespace(now=lambda: datetime(2026, 11, 26, 15, 35)))
    calls = _stop_at_append(monkeypatch)
    out = daily_runner.run()
    assert out["ok"] is True and out["skipped"] is True
    assert out["reason"] == "NYSE closed: a market holiday"
    assert calls == []
    (log,) = run_dir.glob("daily_run_*.json")
    assert json.loads(log.read_text())["skipped"] is True
    # it counts as the day's run, so a catch-up on login that evening doesn't trade either
    assert daily_runner._already_ran_today_successfully() == log


def test_a_weekend_skips_a_real_run(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 28))  # a Saturday
    calls = _stop_at_append(monkeypatch)
    out = daily_runner.run()
    assert out["skipped"] is True and out["reason"] == "NYSE closed: a weekend" and calls == []


def test_a_trading_day_runs(tmp_path, monkeypatch, caplog):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 25))  # the Wednesday before
    calls = _stop_at_append(monkeypatch)
    out = daily_runner.run()
    assert out["ok"] is False and out["error"] == "append_daily failed" and calls == ["append"]
    assert "Early close" not in caplog.text


def test_a_dry_run_still_refreshes_data_on_a_holiday(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 26))
    calls = _stop_at_append(monkeypatch)
    out = daily_runner.run(dry_run=True)
    assert not out.get("skipped") and calls == ["append"]


def test_an_early_close_is_logged_as_by_design(tmp_path, monkeypatch, caplog):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 27))  # closes at 13:00 ET
    calls = _stop_at_append(monkeypatch)
    with caplog.at_level("INFO", logger="daily_runner"):
        daily_runner.run()
    assert calls == ["append"]
    assert "market-on-open" in caplog.text and "by design" in caplog.text


def test_the_catch_up_guard_skips_a_holiday(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 26))
    monkeypatch.setattr(sys, "argv", ["daily_runner", "--skip-if-ran-today"])
    monkeypatch.setattr(daily_runner, "run", lambda **_: pytest.fail("the guard must not start a run"))
    with pytest.raises(SystemExit) as stop:
        daily_runner.main()
    assert stop.value.code == 0
