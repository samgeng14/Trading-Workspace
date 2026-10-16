"""A run started with --progress-file writes what it has done, and the step it is starting,
before each step, so the dashboard can show the run live; the file ends as the run log."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production import daily_runner  # noqa: E402


def test_no_progress_file_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(daily_runner, "PROGRESS_FILE", None)
    daily_runner._progress({"run_id": "x", "steps": {}}, "reconcile")
    assert list(tmp_path.iterdir()) == []


def test_the_file_holds_the_run_so_far_and_the_step_starting(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runs" / "progress.json"
    monkeypatch.setattr(daily_runner, "PROGRESS_FILE", path)
    daily_runner._progress({"run_id": "r1", "steps": {"reconcile": {"ok": True}}}, "append_daily")
    doc = json.loads(path.read_text())
    assert doc["run_id"] == "r1" and doc["steps"] == {"reconcile": {"ok": True}}
    assert doc["current"] == "append_daily" and doc["finished"] is False
    daily_runner._progress({"run_id": "r1", "steps": {}, "ok": True}, None)
    assert json.loads(path.read_text())["finished"] is True
    assert not (tmp_path / "runs" / "progress.json.tmp").exists()


def test_a_full_run_reports_each_step_before_it_starts(tmp_path, monkeypatch) -> None:
    path = tmp_path / "progress.json"
    monkeypatch.setattr(daily_runner, "PROGRESS_FILE", path)
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 10, 14))
    monkeypatch.setattr(daily_runner, "_submit_disabled", lambda cfg, profile: None)
    monkeypatch.setattr(daily_runner, "_load_execution_config", lambda: {})
    monkeypatch.setattr(
        daily_runner,
        "_load_rebalance_config",
        lambda: {"cadence": "drift", "drift_threshold": 0.05, "composition_threshold": 0.1, "cooldown_days": 5},
    )
    monkeypatch.setattr(daily_runner, "_load_latest_targets", lambda: None)
    monkeypatch.setattr(daily_runner, "_current_positions", lambda *a: (None, 1.0, "test"))
    monkeypatch.setattr(daily_runner, "_holdings_outside_model", lambda *a: None)
    monkeypatch.setattr(daily_runner, "_load_reconciliation", lambda profile: None)

    seen: list[tuple[str, str]] = []

    def step(name: str, result: dict):
        def run(*args, **kwargs):
            seen.append((name, json.loads(path.read_text())["current"]))
            return result

        return run

    monkeypatch.setattr(daily_runner, "step_reconcile", step("reconcile", {"ok": True}))
    monkeypatch.setattr(daily_runner, "step_append_daily", step("append_daily", {"ok": True}))
    monkeypatch.setattr(daily_runner, "step_append_indices_daily", step("append_indices_daily", {"ok": True}))
    monkeypatch.setattr(daily_runner, "step_append_futures_daily", step("append_futures_daily", {"ok": True}))
    monkeypatch.setattr(daily_runner, "step_generate_targets", step("generate_targets", {"ok": True}))
    monkeypatch.setattr(
        daily_runner, "_needs_rebalance", step("rebalance_decision", (True, {"rebalance": True, "reason": "forced"}))
    )
    monkeypatch.setattr(daily_runner, "step_build_basket", step("build_basket", {"ok": True, "orders_rows": 3}))
    monkeypatch.setattr(daily_runner, "step_submit_orders", step("submit_orders", {"ok": True, "submitted": True}))

    result = daily_runner.run(profile="paper", force_rebalance=True)

    assert seen == [
        ("reconcile", "reconcile"),
        ("append_daily", "append_daily"),
        ("append_indices_daily", "append_daily"),
        ("append_futures_daily", "append_daily"),
        ("generate_targets", "generate_targets"),
        ("rebalance_decision", "rebalance_decision"),
        ("build_basket", "build_basket"),
        ("submit_orders", "submit_orders"),
    ]
    final = json.loads(path.read_text())
    assert final["finished"] is True and final["current"] is None
    assert final["run_id"] == result["run_id"] and final["steps"]["submit_orders"]["submitted"] is True
    # the run log is written as before
    assert (tmp_path / "artifacts" / "runs" / f"daily_run_{result['run_id']}.json").exists()


@pytest.mark.parametrize("flag", [[], ["--progress-file", "PATH"]])
def test_the_flag_sets_the_file(tmp_path, monkeypatch, flag) -> None:
    monkeypatch.setattr(daily_runner, "PROGRESS_FILE", None)  # restored after the test, whatever main sets
    target = tmp_path / "p.json"
    argv = ["daily_runner", "--dry-run"] + [str(target) if a == "PATH" else a for a in flag]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(daily_runner, "run", lambda **kw: {"ok": True, "skipped": True})
    with pytest.raises(SystemExit):
        daily_runner.main()
    assert daily_runner.PROGRESS_FILE == (target if flag else None)
