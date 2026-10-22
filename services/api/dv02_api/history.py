"""Daily NAV history and daily closes.

NAV history merges, per trading day (later sources win):
  1. account-summary snapshots the engine writes (data/broker/account/*_account_summary_*.csv)
  2. the API's own record of NAV after each close (artifacts/api/nav_<profile>.csv)
  3. IBKR Flex NAV, when configured (artifacts/api/flex_nav_<profile>.csv)
plus the inception capital on the inception date when nothing earlier exists.

Closes come from IBKR's dividend-adjusted daily bars (cached under
artifacts/api/bars, refreshed once a day) and fall back to the engine's
price master, which the daily runner appends every day.
"""

from __future__ import annotations

import csv
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

from . import engine, flex, nyse

log = logging.getLogger(__name__)


# ── inception ──────────────────────────────────────────────────────────


def inception() -> tuple[date, float] | None:
    """A start you set yourself: DV02_INCEPTION_DATE (and DV02_INCEPTION_NAV, default
    $1,000,000) in the environment or .env. Without it, inception is simply the first
    day with a NAV, which is what IBKR Flex gives once it's connected."""
    from src.broker.ibkr import settings  # trading-engine: loads .env

    settings.load_dotenv()
    raw = os.environ.get("DV02_INCEPTION_DATE", "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw), float(os.environ.get("DV02_INCEPTION_NAV", "1000000"))


# ── NAV history ────────────────────────────────────────────────────────


def _snapshot_files(profile: str) -> list[Path]:
    return sorted((engine.data_dir() / "broker" / "account").glob(f"{profile}_account_summary_*.csv"))


def _snapshot_time(path: Path) -> datetime | None:
    stamp = path.stem.rsplit("_", 2)[-2:]
    try:
        return datetime.strptime("_".join(stamp), "%Y%m%d_%H%M%S").astimezone()  # local time
    except ValueError:
        return None


def _read_tags(path: Path) -> dict[str, str]:
    try:
        with path.open() as f:
            return {r["tag"]: r["value"] for r in csv.DictReader(f) if r.get("tag")}
    except (OSError, KeyError):
        return {}


def snapshot_navs(profile: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for path in _snapshot_files(profile):
        when = _snapshot_time(path)
        tags = _read_tags(path)
        try:
            nav = float(tags.get("NetLiquidation", ""))
        except ValueError:
            continue
        if when is not None:
            # before the open it is the previous close; during the session, today's intraday value
            out[nyse.session_state(when).trading_date] = nav
    return out


def _own_path(profile: str) -> Path:
    return engine.api_state_dir() / f"nav_{profile}.csv"


def own_navs(profile: str) -> dict[date, float]:
    path = _own_path(profile)
    if not path.exists():
        return {}
    out = {}
    with path.open() as f:
        for r in csv.DictReader(f):
            try:
                out[date.fromisoformat(r["date"])] = float(r["nav"])
            except (KeyError, ValueError):
                continue
    return out


def record_close_nav(profile: str, trading_date: date, nav: float) -> None:
    """The NAV of a completed session, recorded once: the first value after its closing
    prints settle (nyse.CLOSE_SETTLE). Later values carry after-hours marks, which drift
    from IBKR's statement: up to 0.12% in October 2026, when every pass until midnight
    replaced it. The next day's Flex NAV takes its place on the curve."""
    rows = own_navs(profile)
    if trading_date in rows:
        return
    rows[trading_date] = round(nav, 2)
    path = _own_path(profile)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        for d, v in sorted(rows.items()):
            w.writerow([d.isoformat(), f"{v:.2f}"])
    tmp.replace(path)


@dataclass
class NavHistory:
    navs: list[tuple[date, float]]  # oldest first, trading days only
    sources: list[str]
    complete: bool  # known to start at inception: from IBKR Flex, or a start you configured

    def coverage(self) -> dict:
        return {
            "first_date": (self.navs[0][0] if self.navs else date.today()).isoformat(),
            "days": len(self.navs),
            "complete": self.complete,
            "sources": self.sources,
        }


def nav_history(profile: str, live: tuple[date, float] | None = None) -> NavHistory:
    merged: dict[date, float] = {}
    sources = []
    flex_rows = dict(flex.read_cache(profile))
    for name, rows in (
        ("account snapshots", snapshot_navs(profile)),
        ("recorded closes", own_navs(profile)),
        ("IBKR Flex", flex_rows),
    ):
        if rows:
            sources.append(name)
            merged.update(rows)
    if live is not None:
        merged[live[0]] = live[1]
        sources.append("live NAV")
    merged = {d: v for d, v in merged.items() if v > 0 and nyse.is_trading_day(d)}
    complete = bool(flex_rows)
    configured = inception()
    if configured is not None:
        start, capital = configured
        merged = {d: v for d, v in merged.items() if d >= start}
        if not merged or min(merged) > start:
            merged[start] = capital
            sources.append(f"configured start ${capital:,.0f} on {start}")
        complete = True
    return NavHistory(sorted(merged.items()), sources, complete)


# ── closes ─────────────────────────────────────────────────────────────


def _positive(value: str | None) -> float | None:
    """``value`` as a float when it is finite and > 0, else None: a NaN or zero close is no close."""
    try:
        x = float(value or "")
    except ValueError:
        return None
    return x if math.isfinite(x) and x > 0 else None


def _bars_path(symbol: str) -> Path:
    folder = engine.api_state_dir() / "bars"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{symbol}.csv"


def read_bars(symbol: str) -> list[tuple[date, float]]:
    path = _bars_path(symbol)
    if not path.exists():
        return []
    with path.open() as f:
        out = []
        for r in csv.DictReader(f):
            try:
                d, close = date.fromisoformat(r["date"]), _positive(r["close"])
            except (KeyError, ValueError):
                continue
            if close is not None:
                out.append((d, close))
        return out


def write_bars(symbol: str, bars: list[tuple[date, float]]) -> None:
    path = _bars_path(symbol)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        for d, c in bars:
            w.writerow([d.isoformat(), repr(c)])
    tmp.replace(path)


def bars_stale(symbol: str, now: datetime | None = None) -> bool:
    bars = read_bars(symbol)
    if not bars:
        return True
    s = nyse.session_state(now or datetime.now(timezone.utc))
    last_complete = s.trading_date if s.session != "open" else nyse.prev_trading_day(s.trading_date)
    return bars[-1][0] < last_complete


def _master_path() -> Path:
    return engine.data_dir() / "market" / "cleaned" / "prices" / "etf_prices_master.csv"


@lru_cache(maxsize=4)
def _master(mtime: float) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    with _master_path().open() as f:
        for r in csv.DictReader(f):
            try:
                d, close = date.fromisoformat(r["date"][:10]), _positive(r["close"])
            except (KeyError, ValueError, TypeError):
                continue
            if close is not None:
                out.setdefault(r["symbol"].upper(), []).append((d, close))
    for rows in out.values():
        rows.sort()
    return out


def master_closes(symbol: str) -> list[tuple[date, float]]:
    path = _master_path()
    if not path.exists():
        return []
    return _master(path.stat().st_mtime).get(symbol.upper(), [])


def closes(symbol: str) -> list[tuple[date, float]]:
    """Best daily closes for a symbol: IBKR adjusted bars, else the engine's price master."""
    bars = read_bars(symbol)
    master = master_closes(symbol)
    if bars and (not master or bars[-1][0] >= master[-1][0]):
        return bars
    return master or bars


def benchmark_levels(bench_id: str) -> dict[date, float]:
    if bench_id == "6040":
        spy, agg = dict(closes("SPY")), dict(closes("AGG"))
        days = sorted(set(spy) & set(agg))
        level, out = 100.0, {}
        for prev, d in zip(days, days[1:], strict=False):
            r = 0.6 * (spy[d] / spy[prev] - 1) + 0.4 * (agg[d] / agg[prev] - 1)
            level *= 1 + r
            out[d] = level
        if days:
            out[days[0]] = 100.0
        return out
    return dict(closes(bench_id))


# ── offline positions (when IB Gateway is not running) ─────────────────


def positions_snapshot(profile: str) -> tuple[list[dict], datetime] | None:
    """The engine's last saved positions (build_paper_basket.fetch_positions_df): symbol (local
    symbol for futures and options), quantity, IBKR's average cost, and, from files written since
    they were added, sec_type, multiplier and market_price (IBKR's mark then). None where a file
    doesn't say, or IBKR had no value."""
    path = engine.data_dir() / "broker" / "positions" / f"{profile}_positions_snapshot.csv"
    if not path.exists():
        return None
    with path.open() as f:
        rows = []
        for r in csv.DictReader(f):
            try:
                qty = float(r["current_shares"])
            except (KeyError, ValueError, TypeError):  # TypeError: a line cut short while the engine wrote it
                continue
            if qty and math.isfinite(qty):
                rows.append(
                    {
                        "symbol": r["symbol"].upper(),
                        "quantity": qty,
                        "avg_cost": _positive(r.get("avg_cost")) or 0.0,
                        "sec_type": (r.get("sec_type") or "").strip().upper() or None,
                        "multiplier": _positive(r.get("multiplier")),
                        "market_price": _positive(r.get("market_price")),
                    }
                )
    return rows, datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def latest_snapshot_tags(profile: str) -> tuple[dict[str, str], datetime] | None:
    files = _snapshot_files(profile)
    if not files:
        return None
    path = files[-1]
    return _read_tags(path), (_snapshot_time(path) or datetime.fromtimestamp(path.stat().st_mtime).astimezone())
