"""IBKR Flex Web Service: daily NAV since inception.

The TWS API has no account history, so NAV history comes from a Flex query.
One-time setup in Client Portal (Performance & Reports → Flex Queries):

  1. Create an Activity Flex Query named e.g. "DV02 NAV" with the section
     "Net Asset Value (NAV) in Base" (all fields), format XML. Its period
     doesn't matter: every request names its own dates. Note its Query ID.
  2. Flex Web Service Configuration → enable, generate a token.
  3. Put both in .env at the repo root (gitignored):
         IBKR_FLEX_TOKEN=...
         IBKR_FLEX_QUERY_ID=...

IBKR serves at most 365 days per request, so the first fetch walks back a year
at a time (fd/td) until the account's first NAV. The walk is recorded in
artifacts/api/flex_backfill_<profile>.json; after it, the refresh at startup
and every 6 hours asks for the last few weeks only. Everything is cached in
artifacts/api/flex_nav_<profile>.csv so the API works offline.
"""

from __future__ import annotations

import csv
import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import engine, nyse

log = logging.getLogger(__name__)

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
HEADERS = {"User-Agent": "dv02-api/1.0"}

WINDOW_DAYS = 365  # IBKR's longest fd/td range
MAX_WINDOWS = 10  # ten years back at most
OPENED_SLACK = timedelta(days=7)  # a window whose first NAV comes later than this after its start holds the opening
RECENT_DAYS = 35  # what a refresh asks for once the history is in
SHORT_HISTORY_DAYS = 30
BUSY_CODES = ("1018",)  # too many requests from this token
POLL_CODES = ("1019", "1018")  # statement still generating, or too many requests: ask again
TRANSIENT_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}
NO_STATEMENT_CODES = {"1003"}  # "Statement is not available"


class FlexError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code, self.message = code, message


# ── pacing ─────────────────────────────────────────────────────────────


class Pacer:
    """IBKR allows one SendRequest per second and ten per minute per token, and answers 1018 past
    that. Every SendRequest in this process waits here: at least `gap` seconds apart and at most
    `per_minute` in any rolling minute. It sleeps rather than fails. Clock and sleep are injectable."""

    def __init__(
        self,
        gap: float = 1.2,
        per_minute: int = 10,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gap, self.per_minute = gap, per_minute
        self.clock, self.sleep = clock, sleep
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            while True:
                now = self.clock()
                while self._sent and now - self._sent[0] >= 60.0:
                    self._sent.popleft()
                delay = self._sent[-1] + self.gap - now if self._sent else 0.0
                if len(self._sent) >= self.per_minute:
                    delay = max(delay, self._sent[0] + 60.0 - now)
                if delay <= 0:
                    self._sent.append(now)
                    return
                self.sleep(max(delay, 0.01))  # the floor keeps float rounding from spinning


PACER = Pacer()


# ── requests ───────────────────────────────────────────────────────────


def credentials() -> tuple[str, str] | None:
    from src.broker.ibkr import settings  # trading-engine: loads .env

    settings.load_dotenv()
    token = os.environ.get("IBKR_FLEX_TOKEN", "").strip()
    query = (os.environ.get("IBKR_FLEX_QUERY_ID") or os.environ.get("IBKR_FLEX_NAV_QUERY_ID") or "").strip()
    return (token, query) if token and query else None


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed IBKR host
        return resp.read()


def _fail(root: ET.Element) -> FlexError:
    return FlexError(root.findtext("ErrorMessage") or "", root.findtext("ErrorCode") or "")


def fetch_statement(
    token: str, query_id: str, wait_s: float = 90, fd: date | None = None, td: date | None = None
) -> bytes:
    """One Flex statement. With fd and td (both or neither) IBKR covers those dates instead of the
    query's own period, up to 365 days."""
    if (fd is None) != (td is None):
        raise ValueError("fd and td go together")
    params: dict[str, object] = {"t": token, "q": query_id, "v": 3}
    if fd is not None and td is not None:
        params |= {"fd": f"{fd:%Y%m%d}", "td": f"{td:%Y%m%d}"}
    send = f"{BASE}/SendRequest?{urllib.parse.urlencode(params)}"
    tries = 3
    for attempt in range(tries):
        PACER.wait()
        root = ET.fromstring(_get(send))
        if (root.findtext("Status") or "") == "Success":
            break
        err = _fail(root)
        if err.code not in BUSY_CODES or attempt == tries - 1:
            raise err
        PACER.sleep(30)  # another process is using the token's allowance; let its minute pass
    ref, url = root.findtext("ReferenceCode"), root.findtext("Url") or f"{BASE}/GetStatement"
    deadline = PACER.clock() + wait_s
    while True:
        body = _get(f"{url}?{urllib.parse.urlencode({'t': token, 'q': ref, 'v': 3})}")
        head = body[:400].decode("utf-8", "replace")
        if "<FlexQueryResponse" in head:
            return body
        err = _fail(ET.fromstring(body))
        if err.code not in POLL_CODES or PACER.clock() > deadline:
            raise err
        PACER.sleep(3)


def parse_nav(xml: bytes) -> list[tuple[date, float]]:
    """Daily NAV (base currency) from EquitySummaryByReportDateInBase rows."""
    out: dict[date, float] = {}
    for el in ET.fromstring(xml).iter("EquitySummaryByReportDateInBase"):
        raw, total = el.get("reportDate"), el.get("total")
        if not raw or total in (None, ""):
            continue
        try:
            d = datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
            out[d] = float(total)
        except ValueError:
            continue
    return sorted(out.items())


def _names_dates(exc: FlexError) -> bool:
    return exc.code not in TRANSIENT_CODES and bool(
        re.search(r"\b(dates?|period|range|fd|td|from ?date|to ?date)\b", exc.message, re.IGNORECASE)
    )


def rejects_dates(exc: FlexError) -> bool:
    """IBKR turned down the fd/td range itself, not the token, the query or its own load. 1020 is
    "Invalid request or unable to validate request"; if it meant something else, the plain request
    that follows fails the same way and says so."""
    return exc.code == "1020" or _names_dates(exc)


def fallback_note(exc: FlexError) -> str:
    return (
        f"IBKR refused a date range ({exc}), so this is only the query's own period: "
        "set its Period to Last 365 Calendar Days"
    )


# ── the whole history ──────────────────────────────────────────────────

# Failures on an older window keep what the newer ones loaded; the next refresh walks again.
NETWORK_ERRORS = (OSError, http.client.HTTPException, ET.ParseError)  # URLError, timeouts, resets, junk


@dataclass
class History:
    rows: list[tuple[date, float]]
    windows: int  # statements requested with a date range
    complete: bool  # reached the account's first NAV, IBKR's earliest statement, or the ten-year cap
    date_ranges: bool = True  # False: IBKR refused fd/td and rows are the query's own period
    notes: list[str] = field(default_factory=list)


def _not_yet(td: date, why: object) -> str:
    return f"nothing before {td + timedelta(days=1)} yet ({why}), the next refresh tries again"


def fetch_history(token: str, query_id: str, end: date, max_windows: int = MAX_WINDOWS) -> History:
    """Walk back from `end` in 365-day windows until a window has no NAV, a window's first NAV comes
    well after its start (the account opened inside it), or `max_windows` windows."""
    merged: dict[date, float] = {}
    notes: list[str] = []
    complete = True
    td, windows = end, 0
    while windows < max_windows:
        fd = td - timedelta(days=WINDOW_DAYS - 1)
        windows += 1
        try:
            rows = parse_nav(fetch_statement(token, query_id, fd=fd, td=td))
        except FlexError as exc:
            if windows == 1 and rejects_dates(exc):
                rows = parse_nav(fetch_statement(token, query_id))
                return History(rows, 1, True, date_ranges=False, notes=[fallback_note(exc)])
            if windows == 1:
                raise
            if exc.code in NO_STATEMENT_CODES or _names_dates(exc):
                break  # IBKR has nothing that far back: that is the start
            complete = False
            notes.append(_not_yet(td, exc))
            break
        except NETWORK_ERRORS as exc:
            if windows == 1:
                raise
            complete = False
            notes.append(_not_yet(td, getattr(exc, "reason", None) or str(exc) or type(exc).__name__))
            break
        merged.update(rows)
        held = [d for d, v in rows if v]
        if not held or held[0] > fd + OPENED_SLACK:
            break
        td = fd - timedelta(days=1)
    return History(sorted(merged.items()), windows, complete, notes=notes)


# ── cache ──────────────────────────────────────────────────────────────


def cache_path(profile: str) -> Path:
    return engine.api_state_dir() / f"flex_nav_{profile}.csv"


def marker_path(profile: str) -> Path:
    return engine.api_state_dir() / f"flex_backfill_{profile}.json"


def read_cache(profile: str) -> list[tuple[date, float]]:
    path = cache_path(profile)
    if not path.exists():
        return []
    with path.open() as f:
        rows = []
        for r in csv.DictReader(f):
            try:
                rows.append((date.fromisoformat(r["date"]), float(r["nav"])))
            except (KeyError, ValueError):
                continue
        return rows


def _write_cache(profile: str, navs: dict[date, float]) -> None:
    path = cache_path(profile)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        for d, v in sorted(navs.items()):
            w.writerow([d.isoformat(), f"{v:.2f}"])
    tmp.replace(path)


def read_marker(profile: str) -> dict | None:
    try:
        data = json.loads(marker_path(profile).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, record: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)


def _write_marker(profile: str, walk: History, end: date) -> None:
    """Records the full walk, so later refreshes ask for the last few weeks only."""
    held = [d for d, v in walk.rows if v]
    _write_json(marker_path(profile), {
        "walked_on": date.today().isoformat(),
        "through": end.isoformat(),
        "first_nav": held[0].isoformat() if held else None,
        "windows": walk.windows,
        "complete": walk.complete,
        "date_ranges": walk.date_ranges,
    })  # fmt: skip


def last_business_day(today: date | None = None) -> date:
    """The last completed session: Flex has nothing for today until after the close."""
    return nyse.prev_trading_day(today or nyse.et_date(datetime.now(timezone.utc)))


def _recent_start(marker: dict | None, cached: dict[date, float], end: date) -> date | None:
    """Where an incremental fetch starts, or None when the history needs a full walk."""
    if not marker or not marker.get("complete") or not cached:
        return None
    first = marker.get("first_nav")
    if first and min(cached) > date.fromisoformat(first):
        return None  # the cache lost its start
    since = min(end - timedelta(days=RECENT_DAYS - 1), max(cached))
    return since if since >= end - timedelta(days=WINDOW_DAYS - 1) else None  # a year or more missing: walk


# ── refresh ────────────────────────────────────────────────────────────


def refresh(profile: str, full: bool = False, end: date | None = None) -> str:
    """Fetch and cache; returns a one-line outcome for the logs and status.

    The first time (or with full=True) it walks the whole history back to the account's first NAV;
    after that, one request for the last few weeks."""
    creds = credentials()
    if creds is None:
        return "Flex not configured (IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID in .env)"
    end = end or last_business_day()
    cached = dict(read_cache(profile))
    marker = read_marker(profile)
    since = None if full else _recent_start(marker, cached, end)
    notes: list[str] = []
    walk: History | None = None
    if since is None:
        walk = fetch_history(*creds, end)
        rows, notes = walk.rows, list(walk.notes)
        if not rows:
            raise FlexError("statement has no NAV rows; add 'Net Asset Value (NAV) in Base' to the query")
        if walk.date_ranges and walk.complete and sum(1 for _, v in rows if v) < SHORT_HISTORY_DAYS:
            notes.append("under 30 days in all: the account is that new, or the token's user doesn't own it")
    elif marker and marker.get("date_ranges") is False:
        rows = parse_nav(fetch_statement(*creds))  # IBKR refused date ranges on the walk
    else:
        try:
            rows = parse_nav(fetch_statement(*creds, fd=since, td=end))
        except FlexError as exc:
            if not rejects_dates(exc):
                raise
            rows = parse_nav(fetch_statement(*creds))
            notes.append(fallback_note(exc))
            _write_json(marker_path(profile), {**(marker or {}), "date_ranges": False})  # plain from now on
    if walk is None and not rows:
        notes.append(f"no new NAV from IBKR since {since}")
    cached.update(rows)
    if cached:
        _write_cache(profile, cached)
    if walk is not None:
        _write_marker(profile, walk, end)
    days = sorted(cached)
    head = f"Flex NAV: {len(days)} days, {days[0]} to {days[-1]}" if days else "Flex NAV: none yet"
    return "; ".join([head, *notes])


def main(argv: list[str] | None = None) -> int:
    """python -m dv02_api.flex [profile] [--full]: fetch now and say what happened, in one line.
    --full walks the whole history again instead of asking for the last few weeks."""
    import socket
    import sys
    import urllib.error

    args = sys.argv[1:] if argv is None else argv
    full = "--full" in args
    rest = [a for a in args if a != "--full"]
    if len(rest) > 1 or any(a.startswith("-") for a in rest):
        print("usage: python -m dv02_api.flex [profile] [--full]", file=sys.stderr)
        return 2
    profile = rest[0] if rest else "paper"
    try:
        print(refresh(profile, full=full))
        return 0
    except FlexError as exc:
        print(f"IBKR Flex said no: {exc}", file=sys.stderr)
    except (TimeoutError, socket.timeout):
        print("IBKR's Flex service didn't answer in time; try again in a minute", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"Could not reach IBKR's Flex service: {exc.reason}", file=sys.stderr)
    except (ConnectionError, http.client.HTTPException) as exc:
        print(f"Could not reach IBKR's Flex service: {str(exc) or type(exc).__name__}", file=sys.stderr)
    except ET.ParseError:
        print("IBKR's Flex service returned something that isn't XML; try again in a minute", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
