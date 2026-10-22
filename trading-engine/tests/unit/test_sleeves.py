"""config/sleeves.yaml, the sleeve list the Strategies page shows, stays readable and
in step with the research it cites."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.strategies import sleeves  # noqa: E402


def test_the_list_reads_and_cites_reports_that_exist() -> None:
    rows = sleeves.load()
    assert [s["id"] for s in rows][:2] == ["s0", "h2"]
    assert {s["status"] for s in rows} <= set(sleeves.STATUSES)
    for s in rows:
        assert s["report"] is None or (PROJECT_ROOT.parent / s["report"]).exists(), s["report"]
        assert s["max_drawdown"] is None or -1 < s["max_drawdown"] <= 0
    assert next(s for s in rows if s["id"] == "h2")["since"] == "2026-10-22"


def test_a_bad_status_or_a_repeated_id_is_refused(tmp_path) -> None:
    bad = tmp_path / "sleeves.yaml"
    bad.write_text("sleeves:\n  - {id: x, status: maybe}\n")
    with pytest.raises(ValueError, match="status 'maybe'"):
        sleeves.load(bad)
    bad.write_text("sleeves:\n  - {id: x, status: live}\n  - {id: x, status: failed}\n")
    with pytest.raises(ValueError, match="listed twice"):
        sleeves.load(bad)
