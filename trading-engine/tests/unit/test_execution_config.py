"""config/execution.yaml decides whether a daily run may send orders: a run it
doesn't allow is a dry run that says why."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production import daily_runner, execution_config  # noqa: E402

ON = {"mode": "paper", "allow_submit": True, "dry_run": False}


def test_the_committed_file_lets_the_scheduled_run_send_paper_orders():
    # the owner's decision of 2026-10-16; a change to it must be deliberate
    assert execution_config.load() == ON
    assert execution_config.submit_disabled(execution_config.load(), "paper") is None
    assert execution_config.submit_disabled(execution_config.load(), "live") is not None


@pytest.mark.parametrize(
    ("cfg", "why"),
    [
        ({**ON, "allow_submit": False}, "allow_submit: false"),
        ({**ON, "allow_submit": "true"}, 'allow_submit: "true"'),  # a string is not a yes
        ({**ON, "allow_submit": None}, "allow_submit: null"),  # missing
        ({**ON, "dry_run": True}, "dry_run: true"),
        ({**ON, "dry_run": None}, "dry_run: null"),
        ({**ON, "mode": "live"}, "mode: live, and this run's profile is paper"),
        ({"error": "OSError: gone"}, "could not be read (OSError: gone)"),
    ],
)
def test_anything_but_all_three_set_right_sends_nothing(cfg, why):
    assert why in execution_config.submit_disabled(cfg, "paper")


def test_a_file_that_cannot_be_read_or_parsed_is_an_error(tmp_path):
    assert "error" in execution_config.load(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("execution: [1, 2\n")
    assert "error" in execution_config.load(bad)
    listed = tmp_path / "list.yaml"
    listed.write_text("- execution\n")
    assert "error" in execution_config.load(listed)


# ── the runner ────────────────────────────────────────────────────────────


def _run(tmp_path, monkeypatch, yaml_text: str | None) -> tuple[dict, list[dict]]:
    """One real (not --dry-run) daily run on a trading day, every step stubbed,
    with ``yaml_text`` as config/execution.yaml (None: no file)."""
    if yaml_text is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "execution.yaml").write_text(yaml_text)
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 25))
    ok = lambda **_: {"ok": True}  # noqa: E731
    for step in ("step_reconcile", "step_append_daily", "step_append_indices_daily",
                 "step_append_futures_daily", "step_generate_targets", "step_build_basket"):
        monkeypatch.setattr(daily_runner, step, ok)
    monkeypatch.setattr(daily_runner, "_load_rebalance_config", lambda: dict(daily_runner.DEFAULT_REBALANCE_CONFIG))
    monkeypatch.setattr(daily_runner, "_load_latest_targets", lambda: None)
    monkeypatch.setattr(daily_runner, "_current_positions", lambda *a, **k: (None, None, "none"))
    monkeypatch.setattr(daily_runner, "_holdings_outside_model", lambda *a, **k: None)
    monkeypatch.setattr(daily_runner, "_needs_rebalance", lambda *a, **k: (True, {"reason": "test"}))
    submits: list[dict] = []
    monkeypatch.setattr(daily_runner, "step_submit_orders", lambda **kw: submits.append(kw) or {"ok": True})
    return daily_runner.run(), submits


ALLOWED = """execution:
  mode: paper
  allow_submit: true
  dry_run: false
"""


def test_a_run_the_file_allows_sends_its_orders(tmp_path, monkeypatch):
    result, submits = _run(tmp_path, monkeypatch, ALLOWED)
    assert [s["dry_run"] for s in submits] == [False]
    assert result["dry_run"] is False and "submit_disabled" not in result


@pytest.mark.parametrize(
    ("yaml_text", "why"),
    [
        (ALLOWED.replace("allow_submit: true", "allow_submit: false"), "allow_submit: false"),
        (ALLOWED.replace("dry_run: false", "dry_run: true"), "dry_run: true"),
        (ALLOWED.replace("mode: paper", "mode: live"), "mode: live"),
        (None, "could not be read"),
    ],
)
def test_a_run_the_file_does_not_allow_is_a_dry_run_that_says_why(tmp_path, monkeypatch, yaml_text, why):
    result, submits = _run(tmp_path, monkeypatch, yaml_text)
    # the basket and every pre-trade check still run; the submit step is told it's a dry run
    assert [s["dry_run"] for s in submits] == [True]
    assert result["dry_run"] is True and why in result["submit_disabled"]
    (log,) = (tmp_path / "artifacts" / "runs").glob("daily_run_*.json")
    assert why in json.loads(log.read_text())["submit_disabled"]


def test_dash_dash_dry_run_needs_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 25))
    monkeypatch.setattr(daily_runner, "step_reconcile", lambda **_: {"ok": True})
    monkeypatch.setattr(daily_runner, "step_append_daily", lambda **_: {"ok": False, "error": "stub"})
    result = daily_runner.run(dry_run=True)
    assert result["dry_run"] is True and "submit_disabled" not in result
