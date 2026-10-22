#!/usr/bin/env python3
"""
refresh_now.py — One-shot data refresh.

Pulls fresh ETF bars, positions, and account summary from IBKR right now,
without going through the rebalance / order-submit logic. Use this when you
want the dashboard's NAV / positions snapshot to be current but you don't
need to (or shouldn't) submit orders.

Usage:
    cd /Users/tradingworkspace/TradingWorkspace/trading-engine
    python -m src.production.refresh_now                    # paper, autodetect port
    python -m src.production.refresh_now --port 7497        # explicit
    python -m src.production.refresh_now --profile live     # live account
    python -m src.production.refresh_now --lookback "30 D"  # backfill 30 days
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _autodetect_port(profile: str) -> int:
    # Only ever probes ports for the same profile; see settings.candidate_ports.
    from src.broker.ibkr.settings import detect_port

    return detect_port(profile)


def _refresh_positions_and_account(
    profile: str,
    host: str,
    port: int,
    client_id: int,
) -> tuple[bool, str]:
    try:
        from src.broker.ibkr.client import IBKRClient, IBKRConnectionConfig
        from src.production.runtime.build_paper_basket import (
            fetch_account_snapshot_df,
            fetch_positions_df,
        )
    except Exception as exc:
        return False, f"Could not import broker helpers: {exc}"

    try:
        client = IBKRClient(
            IBKRConnectionConfig(host=host, port=port, client_id=client_id, account=None)
        )
        client.connect()
    except Exception as exc:
        return False, f"IBKR connection failed at {host}:{port}: {exc}"

    try:
        positions_df = fetch_positions_df(client)
        account_df = fetch_account_snapshot_df(client)
    except Exception as exc:
        try:
            client.disconnect()
        except Exception:
            pass
        return False, f"Snapshot fetch failed: {exc}"
    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    pos_dir = PROJECT_ROOT / "data" / "broker" / "positions"
    acc_dir = PROJECT_ROOT / "data" / "broker" / "account"
    pos_dir.mkdir(parents=True, exist_ok=True)
    acc_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pos_path = pos_dir / f"{profile}_positions_snapshot.csv"
    acc_path = acc_dir / f"{profile}_account_summary_{ts}.csv"
    positions_df.to_csv(pos_path, index=False)
    account_df.to_csv(acc_path, index=False)

    return True, f"Refreshed {len(positions_df)} positions; account summary at {ts}."


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot IBKR data refresh.")
    parser.add_argument("--profile", default="paper", choices=["paper", "live"])
    parser.add_argument("--host", default=os.environ.get("IBKR_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="API port. Defaults to autodetect (7497 / 4002 / 4001 / 7496).",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=int(os.environ.get("IBKR_CLIENT_ID", "199")),
        dest="client_id",
    )
    parser.add_argument("--lookback", default="30 D", help="Bar history lookback")
    parser.add_argument(
        "--skip-bars",
        action="store_true",
        help="Skip the daily-bar append (only refresh positions + account).",
    )
    args = parser.parse_args()

    port = args.port if args.port is not None else _autodetect_port(args.profile)

    print("=" * 60)
    print(f"REFRESH NOW — {datetime.now().isoformat(timespec='seconds')}")
    print(f"  Profile : {args.profile}")
    print(f"  Endpoint: {args.host}:{port} (client {args.client_id})")
    print("=" * 60)

    if not args.skip_bars:
        print("→ Step 1: Append daily bars")
        from src.production.pipeline.append_ibkr_daily import append_ibkr_daily

        result = append_ibkr_daily(
            profile=args.profile,
            host=args.host,
            port=port,
            client_id=args.client_id,
            lookback=args.lookback,
        )
        if result.get("ok"):
            print(
                f"  ✓ Appended {result.get('new_rows_added_to_master', 0)} rows · "
                f"{result.get('symbols_with_data', 0)} symbols · latest "
                f"{result.get('latest_date')}"
            )
        else:
            print(f"  ✗ Append failed: {result.get('error')}")
            sys.exit(1)

    print("→ Step 2: Refresh positions + account summary")
    ok, msg = _refresh_positions_and_account(
        args.profile, args.host, port, args.client_id + 5
    )
    if ok:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
