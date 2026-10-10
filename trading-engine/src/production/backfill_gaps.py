#!/usr/bin/env python3
"""
backfill_gaps.py — One-time data gap filler.

Fetches the last N trading days from IBKR and appends any missing rows
to the master price file. Run this once after a data outage or after
the initial setup to bring the CSV up to today.

Usage:
    cd /Users/tradingworkspace/TradingWorkspace/trading-engine
    python -m src.production.backfill_gaps
    # or with options:
    python -m src.production.backfill_gaps --lookback "45 D" --port 4002
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.production.pipeline.append_ibkr_daily import append_ibkr_daily

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill_gaps")

MASTER_PARQUET = (
    PROJECT_ROOT
    / "data"
    / "market"
    / "cleaned"
    / "prices"
    / "etf_prices_master.parquet"
)


def _last_date_in_master() -> str | None:
    """Return the most recent date in the master price file, or None."""
    try:
        import pandas as pd

        if MASTER_PARQUET.exists():
            df = pd.read_parquet(MASTER_PARQUET)
        else:
            csv = MASTER_PARQUET.with_suffix(".csv")
            if not csv.exists():
                return None
            df = pd.read_csv(csv)

        df.columns = [c.lower().strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        latest = df["date"].max()
        return str(latest.date()) if not pd.isna(latest) else None
    except Exception as exc:
        log.warning("Could not read master: %s", exc)
        return None


def _master_dates() -> "list":
    import pandas as pd

    if MASTER_PARQUET.exists():
        df = pd.read_parquet(MASTER_PARQUET, columns=["date"])
    else:
        csv = MASTER_PARQUET.with_suffix(".csv")
        if not csv.exists():
            return []
        df = pd.read_csv(csv, usecols=["date"])
    return sorted(pd.to_datetime(df["date"]).dt.normalize().unique())


def auto_lookback(today: "datetime | None" = None, within_days: int = 365, min_missing: int = 3) -> str | None:
    """The lookback that refills the oldest hole in the last year, or None if there is none.

    A hole is ``min_missing`` or more weekdays without a row: between two dates
    in the file (an outage, or a stale file that later got recent days
    appended) or between the last date and today. Single missing weekdays are
    exchange holidays, not holes.
    """
    import pandas as pd

    today = pd.Timestamp(today or datetime.now()).normalize()
    dates = [d for d in _master_dates() if d >= today - pd.Timedelta(days=within_days)]
    if not dates:
        return f"{within_days} D"
    for prev, nxt in zip([*dates], [*dates[1:], today]):
        missing = len(pd.bdate_range(prev, nxt)) - 2
        if missing >= min_missing:
            return f"{min(within_days, (today - prev).days + 7)} D"
    return None


def backfill(
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 101,
    lookback: str = "45 D",
    profile: str = "paper",
) -> dict:
    last_date = _last_date_in_master()
    today = str(datetime.now().date())

    log.info("=" * 60)
    log.info("IBKR Gap Backfill")
    log.info("  Master last date : %s", last_date or "unknown")
    log.info("  Today            : %s", today)
    log.info("  Lookback window  : %s", lookback)
    log.info("  IBKR endpoint    : %s:%s (client %s)", host, port, client_id)
    log.info("=" * 60)

    result = append_ibkr_daily(
        profile=profile,
        host=host,
        port=port,
        client_id=client_id,
        lookback=lookback,
    )

    if result.get("ok"):
        log.info("✓ Backfill complete.")
        log.info("  Symbols fetched  : %s", result.get("symbols_with_data", 0))
        log.info("  New rows added   : %s", result.get("new_rows_added_to_master", 0))
        log.info("  Latest date now  : %s", result.get("latest_date"))
        log.info("  Snapshot saved   : %s", result.get("snapshot_path"))
    else:
        log.error("✗ Backfill failed: %s", result.get("error"))

    log.info("-" * 60)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill IBKR data gaps")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002, help="4002=paper, 4001=live")
    parser.add_argument("--client-id", type=int, default=101, dest="client_id")
    parser.add_argument(
        "--lookback",
        default="45 D",
        help="IBKR lookback string e.g. '45 D', '3 M'",
    )
    parser.add_argument("--profile", default="paper", choices=["paper", "live"])
    parser.add_argument(
        "--auto",
        action="store_true",
        help="find the oldest hole in the last year and pick the lookback that refills it; no-op if none",
    )
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args()

    if args.auto:
        lookback = auto_lookback()
        if lookback is None:
            log.info("No gaps in the master price file over the last year; nothing to backfill.")
            sys.exit(0)
        log.info("Found a gap; backfilling with lookback %s", lookback)
        args.lookback = lookback

    result = backfill(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        lookback=args.lookback,
        profile=args.profile,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
