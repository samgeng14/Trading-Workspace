"""
exit_test.py — Phase 0's exit test, session by session (docs/PLAN.md §14): ten
sessions in a row with the doctor green, the run executing, the reconcile
matching IBKR to the share, and NAV matching IBKR's statement.

    python -m src.runtime.exit_test                     # the last 15 sessions
    python -m src.runtime.exit_test --since 2026-10-19 --json

Each session's four checks, from the engine's files:

  doctor     that day's last doctor report has no FAIL (artifacts/doctor: kept by
             dv02-doctor.timer before each run, and by the dashboard's Run doctor)
  run        that day's last real run ended ok (artifacts/runs; dry runs don't count)
  reconcile  that run's reconcile found no difference at all, not just none over
             the kill switch's threshold
  nav        the NAV the API recorded at the close (artifacts/api/nav_<profile>.csv)
             is within NAV_TOLERANCE of IBKR's statement, the Flex NAV
             (artifacts/api/flex_nav_<profile>.csv)

A check whose evidence can't be in yet is pending: today's, and Flex's for the
last few sessions (it publishes a day's NAV the next morning). Pending sessions
at the end neither count nor break the streak.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.common import clock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_NEEDED = 10
# The API records the close from IBKR's live marks 15 minutes after it; the
# statement uses the official closes and accruals. 0.10% is about $1,000.
NAV_TOLERANCE = 0.001
FLEX_LAG_SESSIONS = 3  # today and the two sessions before it may still lack a statement
CHECKS = ("doctor", "run", "reconcile", "nav")
PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"


@dataclass
class Check:
    status: str
    note: str = ""


@dataclass
class Session:
    day: date
    checks: dict[str, Check] = field(default_factory=dict)

    @property
    def status(self) -> str:
        states = {c.status for c in self.checks.values()}
        if FAIL in states:
            return FAIL
        return PENDING if PENDING in states else PASS


def _json(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _navs(path: Path) -> dict[date, float]:
    out: dict[date, float] = {}
    try:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    out[date.fromisoformat(row["date"])] = float(row["nav"])
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return out


def _real_run(runs: Path, day: date) -> dict[str, Any] | None:
    """The day's last run that wasn't a dry run. Run logs are named by the
    machine's local start time, the same day as 15:35 New York time."""
    for path in sorted(runs.glob(f"daily_run_{day:%Y%m%d}_*.json"), reverse=True):
        doc = _json(path)
        if doc and not doc.get("dry_run") and not doc.get("skipped"):
            return doc
    return None


def _doctor(report: dict[str, Any] | None, day: date, today: date) -> Check:
    if report is None:
        return Check(PENDING, "the doctor hasn't run yet") if day == today else Check(FAIL, "no doctor report")
    failed = [str(r.get("name")) for r in report.get("results") or [] if r.get("status") == "FAIL"]
    return Check(FAIL, f"doctor failed: {', '.join(failed)}") if failed else Check(PASS)


def _run(run: dict[str, Any] | None, day: date, today: date) -> Check:
    if run is None:
        return Check(PENDING, "the run hasn't run yet") if day == today else Check(FAIL, "no run")
    if run.get("halted"):
        return Check(FAIL, f"halted: {run['halted']}")
    if not run.get("ok"):
        submit = (run.get("steps") or {}).get("submit_orders") or {}
        why = run.get("error") or "; ".join(map(str, submit.get("reasons") or [])) or "see its run log"
        return Check(FAIL, f"the run failed: {why}")
    return Check(PASS)


def _reconcile(run: dict[str, Any] | None, day: date, today: date) -> Check:
    if run is None:
        return Check(PENDING) if day == today else Check(FAIL, "no run, so no reconcile")
    rec = (run.get("steps") or {}).get("reconcile")
    if not rec:
        return Check(FAIL, "the run has no reconcile step")
    if rec.get("error") or not rec.get("ok"):
        return Check(FAIL, f"the reconcile failed: {rec.get('error') or 'see its report'}")
    if rec.get("skipped"):
        return Check(FAIL, f"the reconcile didn't check: {rec.get('reason')}")
    n = len(rec.get("diffs") or [])
    return Check(FAIL, f"the reconcile found {n} difference{'s' * (n != 1)} from IBKR") if n else Check(PASS)


def _nav(close: dict[date, float], flex: dict[date, float], day: date, today: date, recent: set[date],
         tolerance: float) -> Check:  # fmt: skip
    if day not in close:
        if day == today:
            return Check(PENDING, "the close isn't recorded yet")
        return Check(FAIL, "no NAV recorded at the close")
    if day not in flex:
        if day in recent:
            return Check(PENDING, "IBKR's statement isn't in yet")
        return Check(FAIL, "Flex has no NAV for that day")
    gap = close[day] - flex[day]
    note = f"close ${close[day]:,.2f}, statement ${flex[day]:,.2f} ({gap / flex[day]:+.3%})"
    return Check(PASS if abs(gap) <= tolerance * flex[day] else FAIL, note)


def sessions_between(since: date | None, today: date, count: int) -> list[date]:
    """NYSE sessions from ``since`` through ``today``; without ``since``, the last ``count``."""
    days: list[date] = []
    cursor = today
    while (since is None and len(days) < count) or (since is not None and cursor >= since):
        if clock.is_us_business_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def evaluate(
    profile: str = "paper",
    *,
    since: date | None = None,
    today: date | None = None,
    count: int = 15,
    root: Path | None = None,
    tolerance: float = NAV_TOLERANCE,
) -> list[Session]:
    today = today or clock.now_et().date()
    artifacts = (root or PROJECT_ROOT) / "artifacts"
    close = _navs(artifacts / "api" / f"nav_{profile}.csv")
    flex = _navs(artifacts / "api" / f"flex_nav_{profile}.csv")
    days = sessions_between(since, today, count)
    recent = set(sessions_between(None, today, FLEX_LAG_SESSIONS))
    out = []
    for day in days:
        run = _real_run(artifacts / "runs", day)
        report = _json(artifacts / "doctor" / f"doctor_{profile}_{day.isoformat()}.json")
        out.append(Session(day, {
            "doctor": _doctor(report, day, today),
            "run": _run(run, day, today),
            "reconcile": _reconcile(run, day, today),
            "nav": _nav(close, flex, day, today, recent, tolerance),
        }))  # fmt: skip
    return out


def streak(results: list[Session]) -> int:
    """Sessions in a row that passed every check, ending at the latest one decided."""
    tail = list(results)
    while tail and tail[-1].status == PENDING:
        tail.pop()
    n = 0
    for s in reversed(tail):
        if s.status != PASS:
            break
        n += 1
    return n


def render(results: list[Session], profile: str, tolerance: float) -> str:
    n = streak(results)
    lines = [
        f"Phase 0 exit test ({profile}): {SESSIONS_NEEDED} sessions in a row with the doctor green, the run",
        f"ending ok, the reconcile matching IBKR to the share, and the close within {tolerance:.2%} of",
        "IBKR's statement (docs/PLAN.md §14).",
        "",
        "session     " + "".join(f"{c:<11}" for c in CHECKS),
    ]
    for s in results:
        lines.append(f"{s.day.isoformat()}  " + "".join(f"{s.checks[c].status.lower():<11}" for c in CHECKS))
        notes = [c.note for c in s.checks.values() if c.note and (c.status == FAIL or c is s.checks["nav"])]
        lines.extend(f"            {note}" for note in notes)
    done = " Passed." if n >= SESSIONS_NEEDED else ""
    open_days = [s.day.isoformat() for s in results if s.status == PENDING]
    waiting = f" Still open: {', '.join(open_days)}." if open_days else ""
    lines += ["", f"Streak: {n} of {SESSIONS_NEEDED}.{done}{waiting}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 0's exit test, session by session.")
    parser.add_argument("--profile", default="paper")
    parser.add_argument("--since", type=date.fromisoformat, help="first session to show (default: the last 15)")
    parser.add_argument("--nav-tolerance-pct", type=float, default=NAV_TOLERANCE * 100,
                        help="how far the recorded close may be from the statement, in %% of NAV")  # fmt: skip
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    tolerance = args.nav_tolerance_pct / 100
    results = evaluate(args.profile, since=args.since, tolerance=tolerance)
    if args.json:
        doc = {
            "profile": args.profile,
            "needed": SESSIONS_NEEDED,
            "streak": streak(results),
            "nav_tolerance": tolerance,
            "sessions": [
                {"day": s.day.isoformat(), "status": s.status,
                 "checks": {k: {"status": c.status, "note": c.note} for k, c in s.checks.items()}}
                for s in results
            ],
        }  # fmt: skip
        print(json.dumps(doc, indent=2))
    else:
        print(render(results, args.profile, tolerance))
    return 0


if __name__ == "__main__":
    sys.exit(main())
