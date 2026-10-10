"""NYSE calendar and session state, in exchange time (America/New_York).

Same rules as the dashboard's demo calendar (apps/web/src/mocks/calendar.ts),
so demo and real mode agree. Covers 2025-2027; extend HOLIDAYS each year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

HOLIDAYS = {
    date.fromisoformat(d)
    for d in [
        "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
        "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
        "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
        "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    ]
}  # fmt: skip

EARLY_CLOSES = {
    date.fromisoformat(d) for d in ["2025-07-03", "2025-11-28", "2025-12-24", "2026-11-27", "2026-12-24", "2027-11-26"]
}

OPEN = time(9, 30)
PRE_OPEN = time(4, 0)
POST_CLOSE = time(20, 0)

Session = Literal["pre", "open", "post", "closed"]


def close_time(d: date) -> time:
    return time(13, 0) if d in EARLY_CLOSES else time(16, 0)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def et_time(d: date, t: time) -> datetime:
    """The instant the ET wall clock reads ``t`` on ``d``."""
    return datetime.combine(d, t, tzinfo=ET)


def et_date(at: datetime) -> date:
    return at.astimezone(ET).date()


@dataclass(frozen=True)
class SessionState:
    session: Session
    trading_date: date  # current session's date, or the last completed one
    next_change_at: datetime
    next_change: Literal["opens", "closes"]


def session_state(now: datetime) -> SessionState:
    today = et_date(now)
    if is_trading_day(today):
        open_at, close_at = et_time(today, OPEN), et_time(today, close_time(today))
        if now < open_at:
            session: Session = "pre" if now >= et_time(today, PRE_OPEN) else "closed"
            return SessionState(session, prev_trading_day(today), open_at, "opens")
        if now < close_at:
            return SessionState("open", today, close_at, "closes")
        nxt = next_trading_day(today)
        session = "post" if now < et_time(today, POST_CLOSE) else "closed"
        return SessionState(session, today, et_time(nxt, OPEN), "opens")
    nxt = next_trading_day(today)
    return SessionState("closed", prev_trading_day(today), et_time(nxt, OPEN), "opens")
