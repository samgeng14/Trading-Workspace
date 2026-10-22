"""auto_lookback finds holes inside the price master, not just at its end."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.production import backfill_gaps


@pytest.fixture()
def master(tmp_path, monkeypatch):
    path = tmp_path / "etf_prices_master.parquet"
    monkeypatch.setattr(backfill_gaps, "MASTER_PARQUET", path)

    def write(dates: list[pd.Timestamp]) -> None:
        pd.DataFrame({"date": dates, "symbol": "SPY", "close": 1.0}).to_parquet(path)

    return write


def test_a_hole_inside_the_file_is_found(master) -> None:
    # stale through May 6, then the last week of September/October appended
    dates = list(pd.bdate_range("2026-04-01", "2026-05-06")) + list(pd.bdate_range("2026-10-05", "2026-10-09"))
    master(dates)
    assert backfill_gaps.auto_lookback(datetime(2026, 10, 10)) == "164 D"  # back to May 6 plus a week


def test_holidays_are_not_holes(master) -> None:
    dates = [d for d in pd.bdate_range("2026-08-03", "2026-10-09") if d != pd.Timestamp("2026-09-07")]
    master(dates)
    assert backfill_gaps.auto_lookback(datetime(2026, 10, 10)) is None


def test_a_stale_end_is_found(master) -> None:
    master(list(pd.bdate_range("2026-08-03", "2026-09-25")))
    assert backfill_gaps.auto_lookback(datetime(2026, 10, 10)) == "22 D"
