"""``--skip-if-ran-today`` must only honour real (non-dry) successful runs."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
