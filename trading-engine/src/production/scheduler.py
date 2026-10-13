#!/usr/bin/env python3
"""
scheduler.py — Daily auto-trade scheduler.

Two modes:
  1. APScheduler (in-process): runs while this script is alive
  2. macOS LaunchAgent: prints a plist you install once, then macOS
     wakes the script every trading day at the configured time.

Usage:
    # Run the in-process scheduler (keep terminal open or run as service)
    cd /Users/tradingworkspace/TradingWorkspace/trading-engine
    python -m src.production.scheduler

    # Print the macOS LaunchAgent plist (preferred for production)
    python -m src.production.scheduler --print-plist

    # Install the LaunchAgent automatically. Prefer `bin/tradeit schedule install`
    # from the repo root: it schedules this checkout's .venv with the profile,
    # port and client ID the dashboard expects.
    python -m src.production.scheduler --install-launchagent
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from textwrap import dedent

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler")

# ── Config ────────────────────────────────────────────────────────────────────

# Run at 4:32 PM ET (16:32) — 2 minutes after market close, gives IBKR time to settle
HOUR_ET = 16
MINUTE_ET = 32

PYTHON_PATH = sys.executable
RUNNER_MODULE = "src.production.daily_runner"
LAUNCHAGENT_LABEL = "com.capitalfund.daily-runner"
LAUNCHAGENT_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHAGENT_PLIST = LAUNCHAGENT_DIR / f"{LAUNCHAGENT_LABEL}.plist"

LOG_DIR = PROJECT_ROOT / "data" / "logs" / "runtime"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# IBKR API ports — read from env so reinstalling with IBKR_PORT_PAPER=7497
# (TWS) bakes that into the plist.
IBKR_PORT_PAPER = os.environ.get("IBKR_PORT_PAPER", "4002")
IBKR_PORT_LIVE = os.environ.get("IBKR_PORT_LIVE", "4001")

# Optimizer choice — bake into the plist so the daily runner uses it.
CAPITALFUND_OPTIMIZER = os.environ.get("CAPITALFUND_OPTIMIZER", "momentum_tilted")

# ── LaunchAgent plist ─────────────────────────────────────────────────────────

def _build_plist() -> str:
    stdout_log = LOG_DIR / "launchagent_stdout.log"
    stderr_log = LOG_DIR / "launchagent_stderr.log"
    trading_engine_dir = str(PROJECT_ROOT)

    return dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
            "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{LAUNCHAGENT_LABEL}</string>

            <key>ProgramArguments</key>
            <array>
                <string>{PYTHON_PATH}</string>
                <string>-m</string>
                <string>{RUNNER_MODULE}</string>
                <!-- ``--skip-if-ran-today`` makes the RunAtLoad fire below
                     a no-op on every login that happens AFTER today's
                     16:32 already succeeded, while still executing the
                     catch-up when 16:32 was missed (Mac off / asleep). -->
                <string>--skip-if-ran-today</string>
            </array>

            <key>WorkingDirectory</key>
            <string>{trading_engine_dir}</string>

            <key>StartCalendarInterval</key>
            <array>
                <!-- Monday -->
                <dict>
                    <key>Weekday</key><integer>1</integer>
                    <key>Hour</key><integer>{HOUR_ET}</integer>
                    <key>Minute</key><integer>{MINUTE_ET}</integer>
                </dict>
                <!-- Tuesday -->
                <dict>
                    <key>Weekday</key><integer>2</integer>
                    <key>Hour</key><integer>{HOUR_ET}</integer>
                    <key>Minute</key><integer>{MINUTE_ET}</integer>
                </dict>
                <!-- Wednesday -->
                <dict>
                    <key>Weekday</key><integer>3</integer>
                    <key>Hour</key><integer>{HOUR_ET}</integer>
                    <key>Minute</key><integer>{MINUTE_ET}</integer>
                </dict>
                <!-- Thursday -->
                <dict>
                    <key>Weekday</key><integer>4</integer>
                    <key>Hour</key><integer>{HOUR_ET}</integer>
                    <key>Minute</key><integer>{MINUTE_ET}</integer>
                </dict>
                <!-- Friday -->
                <dict>
                    <key>Weekday</key><integer>5</integer>
                    <key>Hour</key><integer>{HOUR_ET}</integer>
                    <key>Minute</key><integer>{MINUTE_ET}</integer>
                </dict>
            </array>

            <!-- Catch-up: fire whenever the agent loads (= every login).
                 The runner's --skip-if-ran-today guard makes this a no-op
                 unless the scheduled 16:32 fire was missed. -->
            <key>RunAtLoad</key>
            <true/>

            <key>StandardOutPath</key>
            <string>{stdout_log}</string>

            <key>StandardErrorPath</key>
            <string>{stderr_log}</string>

            <!-- Auto-restart if it crashes -->
            <key>KeepAlive</key>
            <false/>

            <key>EnvironmentVariables</key>
            <dict>
                <key>IBKR_PORT_PAPER</key><string>{IBKR_PORT_PAPER}</string>
                <key>IBKR_PORT_LIVE</key><string>{IBKR_PORT_LIVE}</string>
                <key>CAPITALFUND_OPTIMIZER</key><string>{CAPITALFUND_OPTIMIZER}</string>
                <key>APP_ENV</key><string>local</string>
            </dict>
        </dict>
        </plist>
    """)


def print_plist() -> None:
    print(_build_plist())


def install_launchagent() -> None:
    LAUNCHAGENT_DIR.mkdir(parents=True, exist_ok=True)
    plist_content = _build_plist()

    LAUNCHAGENT_PLIST.write_text(plist_content)
    log.info("Plist written: %s", LAUNCHAGENT_PLIST)

    # Unload if already loaded (ignore errors)
    subprocess.run(
        ["launchctl", "unload", str(LAUNCHAGENT_PLIST)],
        capture_output=True,
    )

    result = subprocess.run(
        ["launchctl", "load", str(LAUNCHAGENT_PLIST)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        log.info("✓ LaunchAgent installed and loaded.")
        log.info("  It will run daily at %02d:%02d local time (Mon-Fri).", HOUR_ET, MINUTE_ET)
        log.info("  To verify: launchctl list | grep capitalfund")
        log.info("  To remove: launchctl unload %s && rm %s", LAUNCHAGENT_PLIST, LAUNCHAGENT_PLIST)
    else:
        log.error("launchctl load failed: %s", result.stderr)
        sys.exit(1)


def uninstall_launchagent() -> None:
    if LAUNCHAGENT_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHAGENT_PLIST)], capture_output=True)
        LAUNCHAGENT_PLIST.unlink()
        log.info("✓ LaunchAgent uninstalled.")
    else:
        log.info("No LaunchAgent found at %s", LAUNCHAGENT_PLIST)


# ── In-process APScheduler ────────────────────────────────────────────────────

def run_apscheduler(
    hour: int = HOUR_ET,
    minute: int = MINUTE_ET,
    run_now: bool = False,
) -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error("APScheduler not installed. Run: pip install apscheduler --break-system-packages")
        sys.exit(1)

    from src.production.daily_runner import run as daily_run

    def _job() -> None:
        today = datetime.now()
        # Skip weekends (launchd handles this for the LaunchAgent, but APScheduler needs it here)
        if today.weekday() >= 5:
            log.info("Weekend — skipping daily run.")
            return
        log.info("APScheduler triggering daily_runner...")
        try:
            result = daily_run()
            log.info("Daily runner finished: ok=%s", result.get("ok"))
        except Exception:
            log.exception("Daily runner raised an exception")

    scheduler = BlockingScheduler(timezone="America/New_York")
    scheduler.add_job(
        _job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            timezone="America/New_York",
        ),
    )

    log.info("APScheduler started. Will run Mon-Fri at %02d:%02d ET.", hour, minute)
    log.info("Press Ctrl+C to stop.")

    if run_now:
        log.info("--run-now flag: triggering immediately.")
        _job()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Capital Fund daily scheduler")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--print-plist",
        action="store_true",
        help="Print the macOS LaunchAgent plist and exit",
    )
    group.add_argument(
        "--install-launchagent",
        action="store_true",
        help="Write + load the LaunchAgent plist (recommended)",
    )
    group.add_argument(
        "--uninstall-launchagent",
        action="store_true",
        help="Unload and remove the LaunchAgent",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="(APScheduler mode) Also trigger a run immediately on startup",
    )
    parser.add_argument(
        "--hour", type=int, default=HOUR_ET,
        help=f"Hour (local time) to run (default: {HOUR_ET})",
    )
    parser.add_argument(
        "--minute", type=int, default=MINUTE_ET,
        help=f"Minute to run (default: {MINUTE_ET})",
    )
    args = parser.parse_args()

    if args.print_plist:
        print_plist()
    elif args.install_launchagent:
        install_launchagent()
    elif args.uninstall_launchagent:
        uninstall_launchagent()
    else:
        # Default: run APScheduler in-process
        run_apscheduler(hour=args.hour, minute=args.minute, run_now=args.run_now)


if __name__ == "__main__":
    main()
