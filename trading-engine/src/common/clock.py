"""
clock.py — NYSE-aware time utilities.

The trading engine needs to answer questions like:
  * is the market open right now?
  * was yesterday a trading day?
  * how long until the next session close?
  * what was the last trading day before <date>?

We avoid pulling in pandas-market-calendars (heavy dep, slow first-import)
and instead implement a small, fast NYSE calendar with the major US holidays
covered. For dates we don't have explicit holiday rules for (e.g. 2030+) we
fall back to "weekday + not a known holiday" — more permissive than perfect,
but the worst case is the runner attempting an order on a holiday and IBKR
rejecting it, which is recoverable.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

NYSE_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_LOCAL = time(9, 30)
MARKET_CLOSE_LOCAL = time(16, 0)
EARLY_CLOSE_LOCAL = time(13, 0)


# ── Holiday calendar ─────────────────────────────────────────────────────────
# NYSE-observed holidays. Generated rules:
#   - New Year's Day (Jan 1; observed Mon if Sun, Fri if Sat)
#   - MLK Day (3rd Monday Jan)
#   - Presidents Day (3rd Monday Feb)
#   - Good Friday (Easter - 2 days)
#   - Memorial Day (last Monday May)
#   - Juneteenth (Jun 19; observed since 2022)
#   - Independence Day (Jul 4; observed)
#   - Labor Day (1st Monday Sep)
#   - Thanksgiving (4th Thursday Nov)
#   - Christmas (Dec 25; observed)

def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm — Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th occurrence of `weekday` (0=Mon..6=Sun) in (year, month)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last `weekday` in month."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(d: date) -> date:
    """If a holiday lands on a weekend, NYSE observes on adjacent weekday."""
    if d.weekday() == 5:    # Saturday → observed Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:    # Sunday → observed Monday
        return d + timedelta(days=1)
    return d


def _holidays_for_year(year: int) -> set[date]:
    h: set[date] = set()
    h.add(_observed(date(year, 1, 1)))                    # New Year's
    h.add(_nth_weekday(year, 1, 0, 3))                    # MLK
    h.add(_nth_weekday(year, 2, 0, 3))                    # Presidents
    h.add(_easter(year) - timedelta(days=2))              # Good Friday
    h.add(_last_weekday(year, 5, 0))                      # Memorial
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))               # Juneteenth
    h.add(_observed(date(year, 7, 4)))                    # July 4
    h.add(_nth_weekday(year, 9, 0, 1))                    # Labor
    h.add(_nth_weekday(year, 11, 3, 4))                   # Thanksgiving
    h.add(_observed(date(year, 12, 25)))                  # Christmas
    return h


_HOLIDAY_CACHE: dict[int, set[date]] = {}


def _holidays(year: int) -> set[date]:
    if year not in _HOLIDAY_CACHE:
        _HOLIDAY_CACHE[year] = _holidays_for_year(year)
    return _HOLIDAY_CACHE[year]


def is_early_close(d: date) -> bool:
    """True if NYSE closes at 13:00 ET on ``d``.

    The day before Independence Day when July 4 falls Tuesday to Friday, the
    day after Thanksgiving, and Christmas Eve when it is a trading day. Same
    days as EARLY_CLOSES in services/api/dv02_api/nyse.py for 2025-2027.
    """
    if not is_us_business_day(d):
        return False
    if d.month == 7 and d.day == 3:
        return d.weekday() <= 3
    if d.month == 11:
        return d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1)
    return d.month == 12 and d.day == 24


# ── Public API ───────────────────────────────────────────────────────────────

def now_et() -> datetime:
    """Current wall-clock in Eastern Time (NYSE local)."""
    return datetime.now(NYSE_TZ)


def is_us_business_day(d: date) -> bool:
    """True if NYSE is open on the given calendar date."""
    if d.weekday() >= 5:  # Sat / Sun
        return False
    if d in _holidays(d.year):
        return False
    return True


class MarketSession(NamedTuple):
    open_at: datetime
    close_at: datetime


def market_session_for(d: date) -> MarketSession | None:
    """Return the regular session on ``d`` in ET, or None if NYSE is closed.

    On an early-close day (:func:`is_early_close`) the session ends at 13:00.
    """
    if not is_us_business_day(d):
        return None
    close = EARLY_CLOSE_LOCAL if is_early_close(d) else MARKET_CLOSE_LOCAL
    open_dt = datetime.combine(d, MARKET_OPEN_LOCAL).replace(tzinfo=NYSE_TZ)
    close_dt = datetime.combine(d, close).replace(tzinfo=NYSE_TZ)
    return MarketSession(open_at=open_dt, close_at=close_dt)


def is_market_open(at: datetime | None = None) -> bool:
    """True if the regular session is currently open."""
    at = at.astimezone(NYSE_TZ) if at else now_et()
    sess = market_session_for(at.date())
    if sess is None:
        return False
    return sess.open_at <= at < sess.close_at


def next_market_open(after: datetime | None = None) -> datetime:
    """Return the next regular-hours open in ET."""
    cursor = (after.astimezone(NYSE_TZ) if after else now_et()).date()
    # Walk forward up to 14 days — handles every realistic holiday cluster.
    for offset in range(0, 14):
        candidate = cursor + timedelta(days=offset)
        sess = market_session_for(candidate)
        if sess is None:
            continue
        target = (after.astimezone(NYSE_TZ) if after else now_et())
        if sess.open_at > target:
            return sess.open_at
    raise RuntimeError("No market open found in next 14 days — calendar bug?")


def seconds_until_next_close(at: datetime | None = None) -> float:
    """Seconds until the next regular-session close. Useful for runner timing."""
    at = at.astimezone(NYSE_TZ) if at else now_et()
    sess = market_session_for(at.date())
    if sess is not None and at < sess.close_at:
        return (sess.close_at - at).total_seconds()
    # We're past today's close — fall through to next session's close.
    nxt_open = next_market_open(at)
    return (market_session_for(nxt_open.date()).close_at - at).total_seconds()


def previous_business_day(d: date) -> date:
    """Last NYSE business day strictly before ``d``."""
    cursor = d - timedelta(days=1)
    while not is_us_business_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


# IBKR's daily bar for a session takes the closing auction's prints a few
# minutes after the close; until then the bar can still change.
DAILY_BAR_SETTLE = timedelta(minutes=15)


def last_completed_session(at: datetime | None = None) -> date:
    """The latest session whose daily bar is final at ``at``.

    Today's once DAILY_BAR_SETTLE has passed after its close (16:15 ET, or
    13:15 on an early-close day), else the business day before. A run before
    that reads yesterday's close: today's bar is still partial.
    """
    at = at.astimezone(NYSE_TZ) if at else now_et()
    sess = market_session_for(at.date())
    if sess is not None and at >= sess.close_at + DAILY_BAR_SETTLE:
        return at.date()
    return previous_business_day(at.date())
