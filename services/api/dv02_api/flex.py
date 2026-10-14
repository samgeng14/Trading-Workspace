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

The token and the query id travel only in the request URL, to IBKR's own hosts
over https. Nothing here prints, logs or caches them, and IBKR's messages are
scrubbed of them before they become an error.
"""

from __future__ import annotations

import contextlib
import csv
import http.client
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import IO

from . import engine, nyse

log = logging.getLogger(__name__)

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
HEADERS = {"User-Agent": "dv02-api/1.0"}
TIMEOUT_S = 30.0  # per connect and per read, so a silent IBKR can't hang the API's refresh thread

WINDOW_DAYS = 365  # IBKR's longest fd/td range
MAX_WINDOWS = 10  # ten years back at most
OPENED_SLACK = timedelta(days=7)  # a window whose first NAV comes later than this after its start holds the opening
RECENT_DAYS = 35  # what a refresh asks for once the history is in
SHORT_HISTORY_DAYS = 30
SEND_TRIES = 3  # SendRequest answers for a busy token (1018), 30 s apart
BUSY_PAUSE = 30.0  # another process is using the token's allowance; let its minute pass
RETRY_PAUSE = 2.0  # before a dropped connection is tried once more
POLL_PAUSE = 3.0  # between GetStatement polls while the statement generates
BUSY_CODES = ("1018",)  # too many requests from this token
POLL_CODES = ("1019", "1018")  # statement still generating, or too many requests: ask again
TRANSIENT_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}
NO_STATEMENT_CODES = {"1003"}  # "Statement is not available"
DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y")  # the query's Date Format choices that can't be misread
NOT_FLEX = "IBKR's Flex service returned something that isn't a Flex reply; try again in a minute"
NOT_XML_STATEMENT = "IBKR's statement isn't XML: set the query's Format to XML"
REPLY_TAGS = ("FlexStatementResponse", "FlexQueryResponse")


class FlexError(RuntimeError):
    """What IBKR said, on one line, with its error code when it gave one."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code, self.message = code, message


class NotFlex(FlexError):
    """The reply wasn't one of the service's: a maintenance page (which may well parse as XML), an
    empty or cut-off body, or a statement from a query whose Format is CSV or TXT. Printed as it is,
    without an "IBKR said" prefix."""


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

# Failures that can be tried again or noted: URLError (DNS, refused, TLS), HTTPError (4xx/5xx),
# timeouts, resets. None of their messages carry the URL, which holds the token.
NETWORK_ERRORS = (OSError, http.client.HTTPException)


def credentials() -> tuple[str, str] | None:
    from src.broker.ibkr import settings  # trading-engine: loads .env

    settings.load_dotenv()
    token = os.environ.get("IBKR_FLEX_TOKEN", "").strip()
    query = (os.environ.get("IBKR_FLEX_QUERY_ID") or os.environ.get("IBKR_FLEX_NAV_QUERY_ID") or "").strip()
    return (token, query) if token and query else None


def _get(url: str, timeout: float = TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — IBKR's hosts only, see _statement_url
        return resp.read()


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    """IBKR's words on one line, minus the token and the query id should a reply ever echo them."""
    text = " ".join(text.split())
    for secret in secrets:
        if secret:
            text = text.replace(secret, "…")
    return text[:300]


def _xml(body: bytes, statement: bool = False) -> ET.Element:
    """The reply parsed, or NotFlex. A statement that isn't markup at all is a query whose Format
    isn't XML; anything else that won't parse, or parses to something other than a Flex reply (an
    HTML page, nothing, a cut-off body), is IBKR's side."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        head = body.lstrip()[:1]
        if statement and head and head != b"<":
            raise NotFlex(NOT_XML_STATEMENT) from None
        raise NotFlex(NOT_FLEX) from None
    if root.tag not in REPLY_TAGS:
        raise NotFlex(NOT_FLEX)
    return root


def _fail(root: ET.Element, secrets: tuple[str, ...] = ()) -> FlexError:
    """IBKR's refusal as an error. A reply with no error in it (an unexpected shape) still names
    itself, so a failure never reads as an empty reason."""
    code = (root.findtext("ErrorCode") or "").strip()
    message = _scrub(root.findtext("ErrorMessage") or "", secrets)
    if not code and not message:
        status = _scrub(root.findtext("Status") or "", secrets)
        message = f"unexpected reply from IBKR (<{_scrub(root.tag, secrets)}>, status {status!r})"
    return FlexError(message, code)


def _timed_out(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)


def _why(exc: BaseException) -> str:
    """A network failure in a few words for a note or a log line: never the URL (the token is in it)."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if _timed_out(exc):
        return "timed out"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return str(exc) or type(exc).__name__


def _statement_url(advertised: str | None) -> str:
    """Where to fetch the statement: the URL IBKR's reply names, as the protocol says, but only on
    IBKR's own hosts over https, since the token goes in that request. Anything else, or none, gets
    the service's fixed GetStatement. (A URL urllib can't open would also name itself, token and
    all, in its exception.)"""
    advertised = (advertised or "").strip()
    try:
        parts = urllib.parse.urlsplit(advertised)
        host = (parts.hostname or "").lower()
    except ValueError:
        return f"{BASE}/GetStatement"
    ibkr = host == "interactivebrokers.com" or host.endswith(".interactivebrokers.com")
    if parts.scheme == "https" and ibkr and not parts.query and not parts.fragment:
        return advertised
    return f"{BASE}/GetStatement"


def _send(url: str, secrets: tuple[str, ...]) -> ET.Element:
    """SendRequest, paced. A busy token (1018) is asked again after its minute passes, a dropped
    connection once more after a pause; any other refusal is raised as IBKR put it."""
    attempt, retried = 0, False
    while True:
        attempt += 1
        PACER.wait()
        try:
            root = _xml(_get(url))
        except NETWORK_ERRORS:
            if retried:
                raise
            retried = True
            PACER.sleep(RETRY_PAUSE)
            continue
        if (root.findtext("Status") or "").strip() == "Success":
            return root
        err = _fail(root, secrets)
        if err.code not in BUSY_CODES or attempt >= SEND_TRIES:
            raise err
        PACER.sleep(BUSY_PAUSE)


def fetch_statement(
    token: str, query_id: str, wait_s: float = 90, fd: date | None = None, td: date | None = None
) -> bytes:
    """One Flex statement, in IBKR's two steps: SendRequest hands out a reference code, then
    GetStatement serves the statement once it's generated, answering 1019 "in progress" until then
    (polled every few seconds, up to `wait_s`). With fd and td (both or neither) IBKR covers those
    dates instead of the query's own period, up to 365 days."""
    if (fd is None) != (td is None):
        raise ValueError("fd and td go together")
    if fd is not None and td is not None and fd > td:
        raise ValueError("fd is after td")
    secrets = (token, query_id)
    params: dict[str, object] = {"t": token, "q": query_id, "v": 3}
    if fd is not None and td is not None:
        params |= {"fd": f"{fd:%Y%m%d}", "td": f"{td:%Y%m%d}"}
    root = _send(f"{BASE}/SendRequest?{urllib.parse.urlencode(params)}", secrets)
    ref = (root.findtext("ReferenceCode") or "").strip()
    if not ref:
        raise FlexError("IBKR accepted the request but gave no reference code")
    url = f"{_statement_url(root.findtext('Url'))}?{urllib.parse.urlencode({'t': token, 'q': ref, 'v': 3})}"
    deadline = PACER.clock() + wait_s
    while True:
        try:
            body = _get(url)
        except NETWORK_ERRORS:
            if PACER.clock() > deadline:
                raise
            PACER.sleep(POLL_PAUSE)
            continue
        if b"<FlexQueryResponse" in body[:400]:
            return body
        err = _fail(_xml(body, statement=True), secrets)
        if err.code not in POLL_CODES or PACER.clock() > deadline:
            raise err
        PACER.sleep(POLL_PAUSE)


def _date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_nav(xml: bytes) -> list[tuple[date, float]]:
    """Daily NAV (base currency) from the statement's EquitySummaryByReportDateInBase rows, trading
    days only, oldest first, one per date (a date IBKR repeats keeps its last row). The rows can sit
    inside a FlexStatement or straight under FlexQueryResponse. A row without a NAV yet (an empty
    or non-numeric total) is left out; no rows at all is a period with no NAV. Rows that can't be
    read (fields not selected, a Date Format that could be read two ways, more than one account) are
    an error that says what to change in the query."""
    rows = list(_xml(xml, statement=True).iter("EquitySummaryByReportDateInBase"))
    if not rows:
        return []
    if not any(el.get("reportDate") is not None and el.get("total") is not None for el in rows):
        raise FlexError(
            "the NAV rows lack the Report Date or Total field; select all fields of 'Net Asset Value (NAV) in Base'"
        )
    accounts = {el.get("accountId") for el in rows if el.get("accountId")}
    if len(accounts) > 1:
        raise FlexError(f"the statement covers {len(accounts)} accounts; set the query to the paper account only")
    out: dict[date, float] = {}
    unread = ""
    for el in rows:
        raw = el.get("reportDate", "")
        d = _date(raw)
        if d is None:
            unread = unread or raw
            continue
        try:
            v = float(el.get("total", ""))
        except ValueError:
            continue
        if math.isfinite(v) and nyse.is_trading_day(d):
            out[d] = v
    if not out and unread:
        raise FlexError(f"can't read the report dates ({unread!r}); set the query's Date Format to yyyyMMdd")
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


def _note(text: str) -> str:
    return text.replace("; ", ", ")  # bin/tradeit prints each "; "-separated note on its own line


def fallback_note(exc: FlexError) -> str:
    return _note(
        f"IBKR refused a date range ({exc}), so this is only the query's own period: "
        "set its Period to Last 365 Calendar Days"
    )


# ── the whole history ──────────────────────────────────────────────────


@dataclass
class History:
    rows: list[tuple[date, float]]
    windows: int  # statements requested with a date range
    complete: bool  # reached the account's first NAV, IBKR's earliest statement, or the ten-year cap
    date_ranges: bool = True  # False: IBKR refused fd/td and rows are the query's own period
    notes: list[str] = field(default_factory=list)


def _not_yet(td: date, why: object) -> str:
    return _note(f"nothing before {td + timedelta(days=1)} yet ({why}), the next refresh tries again")


def fetch_history(token: str, query_id: str, end: date, max_windows: int = MAX_WINDOWS) -> History:
    """Walk back from `end` in 365-day windows until a window has no NAV, a window's first NAV comes
    well after its start (the account opened inside it), or `max_windows` windows. Failures on an
    older window keep what the newer ones loaded; the next refresh walks again."""
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
            notes.append(_not_yet(td, _why(exc)))
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
    """The cached NAV rows, oldest first, one per date. A line that can't be read (a corrupt or
    half-written file) is skipped, not the rest; a file that can't be opened at all is an empty
    cache, which the next refresh fills by walking the history again."""
    rows: dict[date, float] = {}
    try:
        with cache_path(profile).open(newline="", encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                try:
                    d, v = date.fromisoformat(r["date"]), float(r["nav"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(v):
                    rows[d] = v
    except (OSError, csv.Error):
        pass
    return sorted(rows.items())


def _replace(path: Path, write: Callable[[IO[str]], None]) -> None:
    """Writes `path` through its own temp file beside it, then renames: a reader sees the old file
    or the new one, never a partial one, and two writers at once (the API's refresh, bin/tradeit
    flex, a PortfolioAnalyst import) don't share a temp file. A failed write leaves nothing behind."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_cache(profile: str, navs: dict[date, float]) -> None:
    def body(f: IO[str]) -> None:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        for d, v in sorted(navs.items()):
            if math.isfinite(v):
                w.writerow([d.isoformat(), f"{v:.2f}"])

    _replace(cache_path(profile), body)


def read_marker(profile: str) -> dict | None:
    try:
        data = json.loads(marker_path(profile).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, record: dict) -> None:
    _replace(path, lambda f: f.write(json.dumps(record, indent=2) + "\n"))


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
    try:
        if first and min(cached) > date.fromisoformat(first):
            return None  # the cache lost its start
    except (TypeError, ValueError):
        return None  # a marker that can't be read: walk
    since = min(end - timedelta(days=RECENT_DAYS - 1), max(cached))
    return since if since >= end - timedelta(days=WINDOW_DAYS - 1) else None  # a year or more missing: walk


# ── refresh ────────────────────────────────────────────────────────────


def refresh(profile: str, full: bool = False, end: date | None = None) -> str:
    """Fetch and cache; returns a one-line outcome for the logs and status.

    The first time (or with full=True) it walks the whole history back to the account's first NAV;
    after that, one request for the last few weeks. Either way a day's NAV is whatever IBKR said
    last, and days the walk doesn't reach (a PortfolioAnalyst import) stay."""
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
    import sys

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
    except NotFlex as exc:
        print(exc, file=sys.stderr)
    except FlexError as exc:
        print(f"IBKR Flex said no: {exc}", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        print(f"IBKR's Flex service answered {_why(exc)}; try again in a minute", file=sys.stderr)
    except NETWORK_ERRORS as exc:
        if _timed_out(exc):
            print("IBKR's Flex service didn't answer in time; try again in a minute", file=sys.stderr)
        elif getattr(exc, "filename", None):  # a file, not the network: the cache couldn't be written
            print(f"Could not write the Flex cache: {exc}", file=sys.stderr)
        else:
            print(f"Could not reach IBKR's Flex service: {_why(exc)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
