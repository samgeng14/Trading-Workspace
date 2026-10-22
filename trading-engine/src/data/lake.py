"""
lake.py — The point-in-time data lake (docs/PLAN.md §11, "One point-in-time lake").

Each dataset is Parquet under data/lake/<dataset>/date=<YYYY-MM-DD>/, one folder
per session it describes. Every row carries that session (``as_of``) and when we
knew it (``available_at``, UTC), so a backtest can ask what was known on a day
and see nothing later. Nothing is overwritten: recording a session again adds a
file, and a read "as known at" a moment takes, for each key, the latest row known
by then. DuckDB answers the reads.

    write("futures_curves", rows, as_of=date(2026, 10, 21))
    read("futures_curves", keys=["con_id"], known_at=datetime(2026, 10, 22, tzinfo=timezone.utc))

The lake is git-ignored: it grows every day on the machine that records it.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAKE_DIR = PROJECT_ROOT / "data" / "lake"


def _dataset_dir(dataset: str, root: Path | None) -> Path:
    if not dataset.replace("_", "").isalnum():
        raise ValueError(f"not a dataset name: {dataset!r}")
    return (root or LAKE_DIR) / dataset


def write(
    dataset: str,
    rows: pd.DataFrame,
    *,
    as_of: date,
    available_at: datetime | None = None,
    root: Path | None = None,
) -> Path:
    """Add ``rows`` to ``dataset`` for session ``as_of``, known at ``available_at``
    (default now). Returns the new file; an existing one is never touched."""
    if rows.empty:
        raise ValueError(f"no rows to write to {dataset} for {as_of}")
    known = (available_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out = rows.copy()
    out.insert(0, "as_of", pd.Timestamp(as_of))
    out.insert(1, "available_at", pd.Timestamp(known))
    folder = _dataset_dir(dataset, root) / f"date={as_of.isoformat()}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"part-{known:%Y%m%dT%H%M%S%fZ}.parquet"
    if path.exists():
        raise FileExistsError(path)
    tmp = path.with_suffix(".tmp")
    out.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


def read(
    dataset: str,
    *,
    keys: Sequence[str],
    known_at: datetime | None = None,
    since: date | None = None,
    until: date | None = None,
    root: Path | None = None,
) -> pd.DataFrame:
    """``dataset`` as it was known at ``known_at`` (default now): for each session and
    ``keys``, the latest row recorded by then. ``since`` and ``until`` bound the
    sessions. Empty when nothing was known yet."""
    import duckdb

    folder = _dataset_dir(dataset, root)
    if not any(folder.glob("date=*/*.parquet")):
        return pd.DataFrame()
    known = pd.Timestamp((known_at or datetime.now(timezone.utc)).astimezone(timezone.utc))
    where, params = ["available_at <= ?"], [known]
    if since:
        where.append("as_of >= ?")
        params.append(pd.Timestamp(since))
    if until:
        where.append("as_of <= ?")
        params.append(pd.Timestamp(until))
    partition = ", ".join(["as_of", *(f'"{k}"' for k in keys)])
    sql = f"""
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY {partition} ORDER BY available_at DESC) AS rn
            FROM read_parquet(?, union_by_name = true)
            WHERE {" AND ".join(where)}
        ) WHERE rn = 1
        ORDER BY {partition}
    """
    with duckdb.connect() as con:
        return con.execute(sql, [str(folder / "date=*" / "*.parquet"), *params]).df()
