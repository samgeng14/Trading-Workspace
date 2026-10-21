"""
backfill.py — Load the record the engine kept in files into the store. Every
writer skips or updates what is already there, so running it again is harmless.

    python -m src.storage.backfill

  * the daily runs: artifacts/runs/daily_run_*.json -> run_logs
  * the orders sent: data/broker/orders/paper_orders_submitted_*.csv -> orders
  * the positions: each end-of-day reconcile's IBKR positions
    (data/broker/reconciliations/paper_eod_*.json, "actual") -> positions
  * the NAV: the Flex history (artifacts/api/flex_nav_paper.csv, source flex) and
    the API's record of each close (artifacts/api/nav_paper.csv, api_close) -> nav

Run logs carry their machine's local time without a zone (the Mac's Central,
the VM's UTC); each file's modification time says which (record.zone_from). A
file copied without its times can't say: it is read as UTC, and the log says so.
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from src.storage.db import PROJECT_ROOT, StoreConfig, connect, load_config
from src.storage.record import (
    record_nav,
    record_positions,
    record_run,
    record_submission,
    run_started,
    submission_batch,
    zone_from,
)

log = logging.getLogger(__name__)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _zone(stamp: str, path: Path) -> timezone | None:
    tz = zone_from(stamp, _mtime(path))
    if tz is None:
        log.warning("%s: its file time doesn't say which zone %s is in; reading it as UTC", path.name, stamp)
    return tz


def backfill(conn, root: Path = PROJECT_ROOT, profile: str = "paper") -> dict[str, int]:
    counts = {"runs": 0, "orders": 0, "positions": 0, "nav": 0}

    for path in sorted((root / "artifacts" / "runs").glob("daily_run_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("skipped %s: %s", path.name, exc)
            continue
        if str(doc.get("profile") or profile) != profile:
            continue
        run_id = str(doc.get("run_id") or path.stem.removeprefix("daily_run_"))
        tz = _zone(run_id, path)
        record_run(conn, {**doc, "run_id": run_id}, started_at=run_started(run_id, tz or timezone.utc),
                   ended_at=_mtime(path) if tz else None)  # fmt: skip
        counts["runs"] += 1

    orders_dir = root / "data" / "broker" / "orders"
    for path in sorted(orders_dir.glob(f"{profile}_orders_submitted_*.csv")):
        batch = submission_batch(path)
        if batch is None:
            continue
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if rows and all(str(r.get("status", "")).upper() == "DRY_RUN" for r in rows):
            continue  # a dry run's preview: nothing was sent
        counts["orders"] += record_submission(conn, profile, batch, rows, tz=_zone(batch, path) or timezone.utc)

    for path in sorted((root / "data" / "broker" / "reconciliations").glob(f"{profile}_eod_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(str(doc["generated_at"]))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("skipped %s: %s", path.name, exc)
            continue
        actual = doc.get("actual") or {}
        rows = [{"symbol": s, "current_shares": q} for s, q in actual.items()]
        counts["positions"] += record_positions(conn, profile, at, rows)

    api = root / "artifacts" / "api"
    for name, source in ((f"flex_nav_{profile}.csv", "flex"), (f"nav_{profile}.csv", "api_close")):
        path = api / name
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    day, nav = date.fromisoformat(row["date"]), float(row["nav"])
                except (KeyError, ValueError):
                    continue
                record_nav(conn, profile, day, nav, source=source)
                counts["nav"] += 1
    return counts


def main(cfg: StoreConfig | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = cfg or load_config()
    if not cfg.enabled:
        print(f"The store is off: {cfg.off_because}. Nothing to load.")
        return 0
    with connect(cfg) as conn:
        counts = backfill(conn)
    print(f"{cfg.where}: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
