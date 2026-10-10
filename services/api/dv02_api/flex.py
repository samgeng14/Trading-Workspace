"""IBKR Flex Web Service: daily NAV since inception.

The TWS API has no account history, so NAV history comes from a Flex query.
One-time setup in Client Portal (Performance & Reports → Flex Queries):

  1. Create an Activity Flex Query named e.g. "DV02 NAV" with the section
     "Net Asset Value (NAV) in Base" (all fields), period "Last 365 Calendar
     Days", format XML. Note its Query ID.
  2. Flex Web Service Configuration → enable, generate a token.
  3. Put both in .env at the repo root (gitignored):
         IBKR_FLEX_TOKEN=...
         IBKR_FLEX_QUERY_ID=...

The statement is fetched at startup and every 6 hours, and cached to
artifacts/api/flex_nav_<profile>.csv so the API works offline.
"""

from __future__ import annotations

import csv
import logging
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from . import engine

log = logging.getLogger(__name__)

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
HEADERS = {"User-Agent": "dv02-api/1.0"}


class FlexError(RuntimeError):
    pass


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


def fetch_statement(token: str, query_id: str, wait_s: float = 90) -> bytes:
    q = urllib.parse.urlencode({"t": token, "q": query_id, "v": 3})
    root = ET.fromstring(_get(f"{BASE}/SendRequest?{q}"))
    if (root.findtext("Status") or "") != "Success":
        raise FlexError(f"{root.findtext('ErrorCode')}: {root.findtext('ErrorMessage')}")
    ref, url = root.findtext("ReferenceCode"), root.findtext("Url") or f"{BASE}/GetStatement"
    deadline = time.monotonic() + wait_s
    while True:
        body = _get(f"{url}?{urllib.parse.urlencode({'t': token, 'q': ref, 'v': 3})}")
        head = body[:400].decode("utf-8", "replace")
        if "<FlexQueryResponse" in head:
            return body
        err = ET.fromstring(body)
        code = err.findtext("ErrorCode") or ""
        if code not in ("1019", "1018") or time.monotonic() > deadline:  # 1019: still generating
            raise FlexError(f"{code}: {err.findtext('ErrorMessage')}")
        time.sleep(3)


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


def cache_path(profile: str) -> Path:
    return engine.api_state_dir() / f"flex_nav_{profile}.csv"


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


def refresh(profile: str) -> str:
    """Fetch and cache; returns a one-line outcome for the logs and status."""
    creds = credentials()
    if creds is None:
        return "Flex not configured (IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID in .env)"
    rows = parse_nav(fetch_statement(*creds))
    if not rows:
        raise FlexError("statement has no NAV rows; add 'Net Asset Value (NAV) in Base' to the query")
    existing = dict(read_cache(profile))
    existing.update(rows)
    path = cache_path(profile)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        for d, v in sorted(existing.items()):
            w.writerow([d.isoformat(), f"{v:.2f}"])
    tmp.replace(path)
    return f"Flex NAV: {len(rows)} days through {rows[-1][0]}"
