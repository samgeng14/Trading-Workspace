"""The point-in-time lake (src/data/lake.py): a read sees what was known at a
moment and nothing later, and nothing written is ever overwritten."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("duckdb")
from src.data import lake  # noqa: E402

DAY = date(2026, 10, 21)


def at(hour: int, day: int = 21) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc)


def test_a_read_sees_what_was_known_then(tmp_path) -> None:
    lake.write("curves", pd.DataFrame([{"con_id": 1, "close": 100.0}, {"con_id": 2, "close": 7.0}]),
               as_of=DAY, available_at=at(21), root=tmp_path)  # fmt: skip
    lake.write("curves", pd.DataFrame([{"con_id": 1, "close": 101.5}]), as_of=DAY, available_at=at(23), root=tmp_path)
    assert lake.read("curves", keys=["con_id"], known_at=at(20), root=tmp_path).empty
    first = lake.read("curves", keys=["con_id"], known_at=at(22), root=tmp_path)
    assert list(first["close"]) == [100.0, 7.0]
    latest = lake.read("curves", keys=["con_id"], root=tmp_path)  # now: the correction for 1, the only row for 2
    assert list(latest["close"]) == [101.5, 7.0]
    assert latest["as_of"].dt.date.tolist() == [DAY, DAY]


def test_files_are_per_session_and_never_overwritten(tmp_path) -> None:
    path = lake.write("curves", pd.DataFrame([{"con_id": 1}]), as_of=DAY, available_at=at(21), root=tmp_path)
    assert path.relative_to(tmp_path).as_posix() == "curves/date=2026-10-21/part-20261021T210000000000Z.parquet"
    with pytest.raises(FileExistsError):
        lake.write("curves", pd.DataFrame([{"con_id": 1}]), as_of=DAY, available_at=at(21), root=tmp_path)
    with pytest.raises(ValueError):
        lake.write("curves", pd.DataFrame(), as_of=DAY, root=tmp_path)
    with pytest.raises(ValueError):
        lake.write("../escape", pd.DataFrame([{"x": 1}]), as_of=DAY, root=tmp_path)
    assert not list(tmp_path.rglob("*.tmp"))


def test_sessions_are_bounded_and_a_new_column_reads_empty_before_it(tmp_path) -> None:
    lake.write("curves", pd.DataFrame([{"con_id": 1, "close": 1.0}]), as_of=date(2026, 10, 20),
               available_at=at(21, 20), root=tmp_path)  # fmt: skip
    lake.write("curves", pd.DataFrame([{"con_id": 1, "close": 2.0, "volume": 10.0}]), as_of=DAY,
               available_at=at(21), root=tmp_path)  # fmt: skip
    both = lake.read("curves", keys=["con_id"], root=tmp_path)
    assert list(both["close"]) == [1.0, 2.0] and pd.isna(both["volume"].iloc[0])
    only = lake.read("curves", keys=["con_id"], since=DAY, until=DAY, root=tmp_path)
    assert list(only["close"]) == [2.0]
    assert lake.read("nothing_yet", keys=["con_id"], root=tmp_path).empty
