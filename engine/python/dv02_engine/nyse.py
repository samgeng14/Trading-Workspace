"""The NYSE calendar in New York local time (engine/src/nyse.cpp), a rule-for-rule
port of trading-engine/src/common/clock.py.

    from dv02_engine import nyse
    nyse.is_business_day(date(2026, 11, 26))                    # False: Thanksgiving
    nyse.next_open(datetime(2026, 10, 23, 16, 30, tzinfo=nyse.NY))  # Monday 09:30

Dates are datetime.date. Times are datetime.datetime: one with a zone is
converted to New York local time, a naive one is taken as New York local.
Times come back in New York local time, with the zone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ._core import nyse as _core

NY = ZoneInfo("America/New_York")

Date = _core.Date
Instant = _core.Instant
Session = _core.Session

__all__ = [
    "NY",
    "Date",
    "Instant",
    "Session",
    "easter",
    "from_date",
    "from_instant",
    "holidays",
    "is_business_day",
    "is_early_close",
    "last_completed_session",
    "next_open",
    "previous_business_day",
    "session_for",
    "to_date",
    "to_instant",
    "weekday",
]


def to_date(d: date) -> Date:
    return Date(d.year, d.month, d.day)


def from_date(d: Date) -> date:
    return date(d.year, d.month, d.day)


def to_instant(at: datetime) -> Instant:
    """``at`` as New York local wall-clock time."""
    local = at.astimezone(NY) if at.tzinfo else at
    micros = ((local.hour * 60 + local.minute) * 60 + local.second) * 1_000_000 + local.microsecond
    return Instant(to_date(local.date()), micros)


def from_instant(i: Instant) -> datetime:
    return (datetime.combine(from_date(i.date), time()) + timedelta(microseconds=i.time)).replace(tzinfo=NY)


def weekday(d: date) -> int:
    return _core.weekday(to_date(d))


def easter(year: int) -> date:
    return from_date(_core.easter(year))


def holidays(year: int) -> list[date]:
    """The year's holidays as clock.py lists them (a Saturday New Year's puts the
    Friday before, in the previous year, in this list)."""
    return [from_date(d) for d in _core.holidays(year)]


def is_business_day(d: date) -> bool:
    return _core.is_business_day(to_date(d))


def is_early_close(d: date) -> bool:
    return _core.is_early_close(to_date(d))


def session_for(d: date) -> tuple[datetime, datetime] | None:
    """(open, close) on ``d``, or None when NYSE is closed."""
    s = _core.session_for(to_date(d))
    return None if s is None else (from_instant(s.open), from_instant(s.close))


def next_open(after: datetime) -> datetime:
    return from_instant(_core.next_open(to_instant(after)))


def previous_business_day(d: date) -> date:
    return from_date(_core.previous_business_day(to_date(d)))


def last_completed_session(at: datetime) -> date:
    return from_date(_core.last_completed_session(to_instant(at)))
