"""Staging: the real API over a made-up copy of the paper account, no IBKR needed.

    python -m dv02_api.staging [--scenario fresh|history] [--port 8099] [--now ISO]

The demo (`npm run dev`) has short, tidy labels and a perfect history, so layout
bugs that only real data brings out slip through it. This serves the real app
(create_app, every route and the stream) on the shapes the owner's paper account
had on 2026-10-12: a DU account, NAV about $1.05M with a margin loan of about
$26k, 37 holdings at 102.5% of NAV (the 21-ETF model, TQQQ, and 15 ETFs outside
the model), delayed quotes and no orders today, with the API's own wording for
the schedule, the runs and the doctor. During the session, quotes and P&L move a
little every few seconds and the intraday P&L samples pile up, as they do from a
running API.

Scenarios:
  fresh    what a new setup shows: 1 day of NAV history (today's), the price master
           and targets ending 2026-05-06 (stale), no daily run yet (after 15:35 ET the
           schedule says the run failed to happen), the doctor not run.
  history  an account that has been running: 131 trading days of IBKR Flex NAV
           through the latest close, daily bars for the benchmarks and holdings,
           fresh targets, a daily run every session (today's succeeded once it is
           past 15:35 ET), a doctor report.

Nothing real is read or written: the engine's data tree is a temp folder deleted
on exit, .env is never read, IBKR_* settings in the environment are ignored, and
the dashboard's Run doctor and Submit never reach IB Gateway or start the daily
runner. --now starts the clock at that instant (ISO 8601; without an offset it is
exchange time, ET) and lets it run, so a test can pin a moment inside the session.
The process runs on UTC, so the same --now gives the same page on any machine.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import shutil
import signal
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import engine, nyse
from .broker import AccountSnap, BrokerStatus, PositionSnap
from .universe import BY_SYMBOL, MODEL, STRIP

log = logging.getLogger(__name__)

SCENARIOS = ("fresh", "history")
ACCOUNT = "DU4829316"  # a paper account (DU…)
CASH = -26_301.84  # a margin loan: the book is 102.5% long
NAV_REF = 1_051_943.0  # sizes the holdings: NAV at the close before the snapshot's session
FRESH_MASTER_END = date(2026, 5, 6)  # where the committed price master and targets stop
FLEX_DAYS = 131
TICK_S = 3  # quotes move this often during the session
SAMPLE_S = 15  # intraday P&L samples, as broker.INTRADAY_EVERY_S
START = date(2025, 1, 2)  # daily history starts here (the calendar covers 2025-2027)
CALENDAR = nyse.trading_days(START, date(2027, 12, 31))
_POS = {d: i for i, d in enumerate(CALENDAR)}
MARKET_VOL = 0.009  # the market factor's daily volatility


@dataclass(frozen=True)
class Holding:
    symbol: str
    prev_close: float  # the close before the snapshot's session
    weight: float  # of NAV at that close
    gain: float  # unrealized, against the average cost
    beta: float  # to the market factor
    vol: float  # its own daily volatility on top
    tracks: str | None = None  # a 3x ETF moves with three times this symbol


HOLDINGS = (
    # the model: config/universes/etfs.yaml, etfs.core
    Holding("SPY", 742.15, 0.060, 0.094, 1.0, 0.002),
    Holding("QQQ", 708.40, 0.050, 0.121, 1.2, 0.004),
    Holding("VEA", 72.35, 0.035, 0.071, 0.8, 0.005),
    Holding("VWO", 61.80, 0.030, 0.088, 0.85, 0.007),
    Holding("INDA", 51.20, 0.020, -0.034, 0.6, 0.008),
    Holding("VFH", 129.85, 0.030, 0.046, 1.05, 0.006),
    Holding("VHT", 276.40, 0.030, 0.018, 0.7, 0.006),
    Holding("VIS", 351.10, 0.035, 0.062, 1.0, 0.005),
    Holding("VNQ", 96.25, 0.025, -0.012, 0.9, 0.008),
    Holding("VPU", 201.30, 0.030, 0.035, 0.5, 0.008),
    Holding("TLT", 87.15, 0.035, 0.009, -0.15, 0.007),
    Holding("IEI", 118.70, 0.035, 0.006, -0.05, 0.0025),
    Holding("SHY", 82.55, 0.035, 0.004, -0.01, 0.0006),
    Holding("BIL", 91.60, 0.045, 0.002, 0.0, 0.00005),
    Holding("GLD", 445.20, 0.050, 0.087, 0.05, 0.010),
    Holding("SLV", 72.80, 0.035, 0.104, 0.2, 0.020),
    Holding("USO", 118.40, 0.040, -0.116, 0.3, 0.020),
    Holding("UNG", 11.25, 0.015, 0.077, 0.1, 0.035),
    Holding("IBIT", 49.85, 0.025, 0.079, 1.3, 0.025),
    Holding("ETHA", 19.40, 0.015, 0.095, 1.8, 0.035),
    Holding("VIXY", 25.60, 0.010, -0.055, -4.0, 0.020),
    # left the model on 2026-10-12; the next rebalance sells it
    Holding("TQQQ", 74.90, 0.030, 0.183, 0.0, 0.0, tracks="QQQ"),
    # outside the model
    Holding("DBC", 24.10, 0.020, 0.031, 0.3, 0.009),
    Holding("XLE", 94.50, 0.025, -0.024, 0.7, 0.012),
    Holding("XLP", 79.80, 0.020, 0.012, 0.5, 0.006),
    Holding("XLU", 86.40, 0.025, 0.041, 0.5, 0.008),
    Holding("TIP", 111.20, 0.025, 0.007, -0.05, 0.003),
    Holding("EWY", 98.60, 0.015, 0.214, 1.1, 0.010),
    Holding("SOXX", 312.50, 0.020, 0.167, 1.6, 0.012),
    Holding("EFA", 94.30, 0.025, 0.058, 0.8, 0.005),
    Holding("IWM", 251.40, 0.025, 0.039, 1.2, 0.006),
    Holding("EWJ", 81.20, 0.015, 0.044, 0.7, 0.007),
    Holding("XLK", 302.10, 0.025, 0.133, 1.25, 0.005),
    Holding("HYG", 80.65, 0.020, 0.011, 0.3, 0.002),
    Holding("LQD", 110.90, 0.020, 0.005, -0.05, 0.003),
    Holding("EEM", 55.30, 0.015, 0.079, 0.85, 0.007),
    Holding("XLV", 152.40, 0.015, -0.008, 0.7, 0.006),
)
BY_HOLDING = {h.symbol: h for h in HOLDINGS}
# benchmarks the account doesn't hold: daily bars only
EXTRA = {
    "ACWI": Holding("ACWI", 141.85, 0.0, 0.0, 0.9, 0.003),
    "AGG": Holding("AGG", 100.42, 0.0, 0.0, -0.05, 0.0025),
}
# the committed price master's last closes (2026-05-06) and that day's targets
MAY6 = {
    "USO": (133.95, 0.1372), "SLV": (70.13, 0.0650), "TQQQ": (71.57, 0.0474), "GLD": (430.96, 0.0456),
    "VEA": (70.87, 0.0404), "VIS": (345.38, 0.0452), "VWO": (60.75, 0.0400), "QQQ": (695.77, 0.0377),
    "VNQ": (97.09, 0.0413), "SPY": (733.83, 0.0377), "VPU": (198.05, 0.0444), "VHT": (273.10, 0.0383),
    "VFH": (127.59, 0.0418), "BIL": (91.42, 0.0586), "SHY": (82.30, 0.0450), "IEI": (118.06, 0.0428),
    "TLT": (86.08, 0.0386), "INDA": (50.03, 0.0343), "VIXY": (27.09, 0.0325), "IBIT": (46.19, 0.0309),
    "UNG": (10.45, 0.0270), "ETHA": (17.72, 0.0282),
}  # fmt: skip
MASTER_SYMBOLS = sorted(MAY6)  # the model plus TQQQ, whose prices stay in the master for research
# the ticker strip's closes the session moves from: (prev close, what moves it)
STRIP_CLOSE = {
    "SPX": (7443.61, "SPY"), "NDX": (28946.20, "QQQ"), "RUT": (2521.37, "IWM"), "VIX": (16.42, None),
    "TNX": (4.118, "TLT"), "GLD": (445.20, "GLD"), "USO": (118.40, "USO"), "IBIT": (49.85, "IBIT"),
    "EUR": (1.1617, None),
}  # fmt: skip


def _seed(*parts: object) -> int:
    return zlib.crc32(":".join(map(str, parts)).encode())


def quantities() -> list[int]:
    return [round(h.weight * NAV_REF / h.prev_close) for h in HOLDINGS]


def avg_cost(h: Holding) -> float:
    return round(h.prev_close / (1 + h.gain), 4)


# ── the clock ──────────────────────────────────────────────────────────


class Clock:
    """Wall time, shifted to start at ``start`` (now when None)."""

    def __init__(self, start: datetime | None = None) -> None:
        self.offset = timedelta(0) if start is None else start - datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return datetime.now(timezone.utc) + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += timedelta(seconds=seconds)


def parse_now(text: str) -> datetime:
    """ISO 8601; without an offset it is exchange time (ET)."""
    at = datetime.fromisoformat(text.strip())
    return (at if at.tzinfo else at.replace(tzinfo=nyse.ET)).astimezone(timezone.utc)


def pin_timezone(name: str = "UTC") -> None:
    """The daily run's time is 15:35 ET in the local zone; one zone everywhere makes --now repeatable."""
    os.environ["TZ"] = name
    time.tzset()


# ── one session, tick by tick ──────────────────────────────────────────


class Day:
    """A session's prices every TICK_S from the open: the market factor, each holding
    (beta to it plus its own noise, an opening gap, the day's drift), and the strip."""

    def __init__(self, d: date) -> None:
        self.date = d
        self.open = nyse.et_time(d, nyse.OPEN)
        self.close = nyse.et_time(d, nyse.close_time(d))
        n = int((self.close - self.open).total_seconds() // TICK_S)
        self.ticks = n
        rng = np.random.default_rng(_seed("day", d.isoformat()))
        drift = rng.normal(0.0004, 0.006)  # the market's move today
        market = _walk(rng, n, drift, 0.007, gap=0.002)
        logs: dict[str, np.ndarray] = {}
        for h in HOLDINGS:
            if h.tracks is None:
                logs[h.symbol] = h.beta * market + _walk(rng, n, 0.0, h.vol * 0.8, gap=h.vol * 0.3)
        for h in HOLDINGS:
            if h.tracks is not None:
                logs[h.symbol] = 3 * logs[h.tracks]
        self.prices = np.array([np.round(h.prev_close * np.exp(logs[h.symbol]), 2) for h in HOLDINGS])
        qty = np.array(quantities(), dtype=float)
        prev = np.array([h.prev_close for h in HOLDINGS])
        self.pnl = (qty[:, None] * (self.prices - prev[:, None])).sum(axis=0)
        index = {h.symbol: i for i, h in enumerate(HOLDINGS)}
        strip: dict[str, np.ndarray] = {}
        for symbol, (close, driver) in STRIP_CLOSE.items():
            if symbol == "VIX":
                level = close * np.exp(-5 * market)
            elif symbol == "TNX":  # a yield moves against the bond's price: TLT's duration is about 16.5 years
                level = close - 100 * np.log(self.prices[index["TLT"]] / BY_HOLDING["TLT"].prev_close) / 16.5
            elif symbol == "EUR":
                level = close * np.exp(_walk(rng, n, 0.0, 0.004))
            else:
                level = close * self.prices[index[driver]] / BY_HOLDING[driver].prev_close
            strip[symbol] = np.round(level, 4 if symbol == "EUR" else 3 if symbol == "TNX" else 2)
        self.strip = strip

    def tick(self, at: datetime) -> int:
        """The last tick at or before ``at``, within the session."""
        seconds = (at - self.open).total_seconds()
        return int(min(max(seconds // TICK_S, 0), self.ticks))

    def tick_time(self, k: int) -> datetime:
        return self.open + timedelta(seconds=k * TICK_S)


def _walk(rng: np.random.Generator, n: int, drift: float, vol: float, gap: float = 0.0) -> np.ndarray:
    """A log-price path over n ticks (n + 1 points from the open) with this day's drift and volatility."""
    steps = rng.normal(drift / n, vol / math.sqrt(n), n)
    path = np.concatenate([[0.0], np.cumsum(steps)])
    return path + rng.normal(0.0, gap) if gap else path


@lru_cache(maxsize=8)
def day(d: date) -> Day:
    return Day(d)


# ── the broker ─────────────────────────────────────────────────────────


class StagingBroker:
    """What the API reads from IBKR (broker.Broker's reads), from the made-up account.
    Connected, delayed quotes (type 3), no orders. ``sample_from`` is when the API
    "started" sampling intraday P&L: the fresh scenario starts now, history at the open."""

    def __init__(self, clock: Callable[[], datetime], sample_from: datetime | None = None) -> None:
        from src.broker.ibkr import settings  # trading-engine

        self.clock = clock
        self.sample_from = sample_from
        self.endpoint = settings.resolve_endpoint("paper", host="127.0.0.1", port=4002)
        self.client_id = self.endpoint.client_id("dashboard")
        self.status = BrokerStatus(
            connected=True,
            endpoint=f"{self.endpoint.host}:{self.endpoint.port}",
            account=ACCOUNT,
            since=clock(),
            market_data_type=3,
        )
        self.qty = quantities()

    @property
    def connected(self) -> bool:
        return True

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def market_data_type(self) -> int:
        return 3  # delayed: a paper login without market data shared from the live one

    def _now(self) -> tuple[Day, int, datetime]:
        now = self.clock()
        session = day(nyse.session_state(now).trading_date)
        return session, session.tick(now), now

    def snapshot(self) -> AccountSnap:
        session, k, now = self._now()
        positions = []
        for i, (h, q) in enumerate(zip(HOLDINGS, self.qty, strict=True)):
            last = float(session.prices[i][k])
            cost = avg_cost(h)
            positions.append(
                PositionSnap(
                    con_id=9_100_001 + i, symbol=h.symbol, root=h.symbol,
                    # as Broker.snapshot: ETF for the universe's ETFs, IBKR's STK for any other
                    sec_type="ETF" if h.symbol in BY_SYMBOL else "STK",
                    quantity=float(q), multiplier=1.0, avg_cost=cost, last=last, prev_close=h.prev_close,
                    market_value=q * last, unrealized_pnl=q * (last - cost), day_pnl=q * (last - h.prev_close),
                )
            )  # fmt: skip
        value = sum(p.market_value for p in positions)
        return AccountSnap(
            account=ACCOUNT, currency="USD", nav=round(CASH + value, 2), cash=CASH,
            day_pnl=round(float(session.pnl[k]), 2), as_of=now, positions=positions,
        )  # fmt: skip

    def quotes(self) -> list[dict[str, Any]]:
        session, k, _ = self._now()
        out = []
        for symbol, label, kind, _spec in STRIP:
            close = STRIP_CLOSE[symbol][0]
            last = float(session.strip[symbol][k])
            out.append(
                {
                    "symbol": symbol, "label": label, "last": last, "change": last - close,
                    "change_pct": last / close - 1, "kind": kind,
                    "_type": 1 if kind == "fx" else 3,  # IBKR's FX quotes are free and real-time
                }
            )  # fmt: skip
        return out

    def trades(self) -> list[Any]:
        return []  # no orders today

    async def refresh_orders(self) -> None: ...

    async def cancel(self, trade: Any) -> None:
        raise RuntimeError("the staging account has no orders to cancel")

    def intraday(self, trading_date: date) -> list[tuple[datetime, float]]:
        """P&L every SAMPLE_S from when sampling started, up to now or the close."""
        session = day(trading_date)
        now = self.clock()
        first = session.open
        if self.sample_from is not None and self.sample_from > first:
            steps = math.ceil((self.sample_from - first).total_seconds() / SAMPLE_S)
            first = session.open + timedelta(seconds=steps * SAMPLE_S)
        out = []
        t = first
        while t <= min(now, session.close):
            out.append((t, round(float(session.pnl[session.tick(t)]), 2)))
            t += timedelta(seconds=SAMPLE_S)
        return out


# ── daily history ──────────────────────────────────────────────────────


def _spec(symbol: str) -> Holding:
    return BY_HOLDING.get(symbol) or EXTRA[symbol]


@lru_cache(maxsize=1)
def _market_daily() -> np.ndarray:
    return np.random.default_rng(_seed("market")).normal(0.0004, MARKET_VOL, len(CALENDAR))


@lru_cache(maxsize=64)
def _daily_log_returns(symbol: str) -> np.ndarray:
    """Every calendar day's log return, the same in each scenario: fresh's history is a prefix of history's."""
    if symbol == "NAV":  # the account: about 0.7 beta, a little of its own
        own = np.random.default_rng(_seed("own", symbol)).normal(0.0, 0.003, len(CALENDAR))
        return 0.7 * _market_daily() + own
    h = _spec(symbol)
    if h.tracks is not None:
        return 3 * _daily_log_returns(h.tracks) - 0.0002  # daily reset decay
    own = np.random.default_rng(_seed("own", symbol)).normal(0.0001, h.vol, len(CALENDAR))
    return h.beta * _market_daily() + own


def daily_path(symbol: str, through: date, anchors: dict[date, float], decimals: int = 2) -> list[tuple[date, float]]:
    """Closes from START through ``through``, bent to pass exactly through ``anchors``."""
    n = _POS[through] + 1
    walk = np.cumsum(_daily_log_returns(symbol)[:n])
    pts = sorted((_POS[d], math.log(v) - walk[_POS[d]]) for d, v in anchors.items() if d in _POS and _POS[d] < n)
    fix = np.interp(np.arange(n), [i for i, _ in pts], [v for _, v in pts])
    closes = np.round(np.exp(walk + fix), decimals)
    return list(zip(CALENDAR[:n], closes.tolist(), strict=True))


@dataclass(frozen=True)
class Moment:
    """Where the clock stands: the snapshot's session, the close before it, and the latest close."""

    now: datetime
    session: date
    prev: date
    last_close: date
    complete: bool  # the session has closed

    @classmethod
    def at(cls, now: datetime) -> Moment:
        state = nyse.session_state(now)
        d = state.trading_date
        complete = state.session != "open"
        return cls(now, d, nyse.prev_trading_day(d), d if complete else nyse.prev_trading_day(d), complete)

    def anchors(self, symbol: str) -> dict[date, float]:
        """Where a symbol's daily closes must land: the committed master's 2026-05-06 close, the
        holding's close before the session and, once it has closed, the session's close."""
        out: dict[date, float] = {}
        if symbol in MAY6 and FRESH_MASTER_END < self.prev:
            out[FRESH_MASTER_END] = MAY6[symbol][0]
        out[self.prev] = _spec(symbol).prev_close
        if self.complete and symbol in BY_HOLDING:
            out[self.session] = float(day(self.session).prices[HOLDINGS.index(BY_HOLDING[symbol])][-1])
        return out

    def nav_at_close(self) -> dict[date, float]:
        value = sum(q * h.prev_close for q, h in zip(quantities(), HOLDINGS, strict=True))
        out = {self.prev: CASH + value}
        if self.complete:
            out[self.session] = out[self.prev] + float(day(self.session).pnl[-1])
        return out


# ── the engine's data tree ─────────────────────────────────────────────


def build_tree(root: Path, scenario: str, now: datetime) -> None:
    """The engine files the API reads, as the scenario has them. Writes only under ``root``."""
    from .app import BAR_SYMBOLS

    m = Moment.at(now)
    data, artifacts = root / "data", root / "artifacts"
    (artifacts / "runs").mkdir(parents=True, exist_ok=True)
    api = engine.api_state_dir()  # artifacts/api under root (engine.ENGINE_ROOT points there)
    if scenario == "fresh":
        master = {s: daily_path(s, FRESH_MASTER_END, {FRESH_MASTER_END: MAY6[s][0]}) for s in MASTER_SYMBOLS}
        _write_master(data, master)
        _write_targets(data, FRESH_MASTER_END, {s: (MAY6[s][1], MAY6[s][0]) for s in MAY6}, master)
        return
    # history
    master = {s: daily_path(s, m.last_close, m.anchors(s)) for s in MASTER_SYMBOLS}
    _write_master(data, master)
    model = [i.symbol for i in MODEL]
    total = sum(BY_HOLDING[s].weight for s in model)
    _write_targets(data, m.last_close, {s: (BY_HOLDING[s].weight / total, master[s][-1][1]) for s in model}, master)
    bars_from = m.last_close - timedelta(days=730)  # the API asks IBKR for 2 years
    for s in dict.fromkeys([*BAR_SYMBOLS, *(h.symbol for h in HOLDINGS)]):
        rows = master.get(s) or daily_path(s, m.last_close, m.anchors(s))
        _write_csv(
            api / "bars" / f"{s}.csv", ["date", "close"], [(d.isoformat(), c) for d, c in rows if d >= bars_from]
        )
    # IBKR Flex: daily NAV since the account opened, 131 sessions ago with $1M
    first = CALENDAR[_POS[m.last_close] - FLEX_DAYS + 1]
    navs = daily_path("NAV", m.last_close, {first: 1_000_000.0, **m.nav_at_close()})
    _write_csv(
        api / "flex_nav_paper.csv", ["date", "nav"], [(d.isoformat(), f"{v:.2f}") for d, v in navs if d >= first]
    )
    _write_runs(artifacts / "runs", m)
    report = doctor_report(StagingBroker(lambda: now), now - timedelta(minutes=4), m)
    (api / "doctor_paper.json").write_text(json.dumps(report, indent=2))


def _write_csv(path: Path, head: list[str], rows: list[tuple[Any, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def _write_master(data: Path, master: dict[str, list[tuple[date, float]]]) -> None:
    """etf_prices_master.csv and .parquet, as the engine keeps them: one row per symbol and day."""
    import pandas as pd

    rows = []
    for s in sorted(master):
        rng = np.random.default_rng(_seed("ohlc", s))
        prev = master[s][0][1]
        for d, c in master[s]:
            o = round(prev * (1 + rng.normal(0, 0.002)), 2)
            spread = abs(rng.normal(0, 0.004))
            rows.append((d, s, o, round(max(o, c) * (1 + spread), 2), round(min(o, c) * (1 - spread), 2), c,
                         float(int(rng.integers(400_000, 40_000_000)))))  # fmt: skip
            prev = c
    df = pd.DataFrame(rows, columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    folder = data / "market" / "cleaned" / "prices"
    folder.mkdir(parents=True, exist_ok=True)
    df.assign(date=df["date"].map(date.isoformat)).to_csv(folder / "etf_prices_master.csv", index=False)
    df.assign(date=pd.to_datetime(df["date"])).to_parquet(folder / "etf_prices_master.parquet", index=False)


def _write_targets(
    data: Path, on: date, weights: dict[str, tuple[float, float]], master: dict[str, list[tuple[date, float]]]
) -> None:
    """etf_targets_monthly.parquet (and .csv) as generate_targets writes them, for one rebalance date."""
    import pandas as pd

    def momentum(s: str) -> float:
        closes = [c for d, c in master.get(s, []) if d <= on]
        return closes[-1] / closes[-127] - 1 if len(closes) > 126 else 0.0

    ranked = sorted(weights, key=momentum, reverse=True)
    df = pd.DataFrame(
        [
            {
                "rebalance_date": pd.Timestamp(on), "symbol": s, "target_weight": weights[s][0],
                "signal_value": momentum(s), "rank": i + 1, "reference_price": weights[s][1],
                "obs_count": len(master.get(s, [])),
            }
            for i, s in enumerate(ranked)
        ]
    )  # fmt: skip
    folder = data / "market" / "cleaned" / "targets"
    folder.mkdir(parents=True, exist_ok=True)
    df.to_parquet(folder / "etf_targets_monthly.parquet", index=False)
    df.assign(rebalance_date=df["rebalance_date"].dt.date.map(date.isoformat)).to_csv(
        folder / "etf_targets_monthly.csv", index=False
    )


OUTSIDE = sorted(h.symbol for h in HOLDINGS if h.symbol not in {i.symbol for i in MODEL})


def _write_runs(folder: Path, m: Moment) -> None:
    """A daily run every session at the runner's time (15:35 ET), as the LaunchAgent fires it, up to now: a
    rebalance two sessions back (then a cooldown), one that couldn't reach IB Gateway, the rest holding."""
    from . import runs

    at = runs.runner_time()
    local_today = m.now.astimezone().date()
    days = [
        d
        for d in nyse.trading_days(local_today - timedelta(days=24), local_today)
        if datetime.combine(d, at).astimezone() <= m.now
    ][-12:]
    for n, d in enumerate(reversed(days)):  # n sessions back
        prev = nyse.prev_trading_day(d).isoformat()
        started = datetime.combine(d, at).replace(second=1 + n % 4)
        run_id = f"{started:%Y%m%d_%H%M%S}"
        bars = {"ok": True, "new_rows_added_to_master": 22, "latest_date": prev, "errors": []}
        steps: dict[str, Any] = {
            "append_daily": bars,
            "append_indices_daily": {"ok": True, "new_rows_added_to_master": 4, "latest_date": prev, "errors": []},
            "append_futures_daily": {"ok": True, "new_rows_added_to_master": 0, "latest_date": prev,
                                     "errors": ["MES: no market data permissions (354)", "ZN: no data (162)"]},
            "generate_targets": {"ok": True, "optimizer": "momentum_tilted", "latest_rebalance_date": prev},
        }  # fmt: skip
        doc: dict[str, Any] = {"run_id": run_id, "date": d.isoformat(), "profile": "paper", "dry_run": False}
        if n == 5:
            doc.update(ok=False, error="append_daily failed")
            doc["steps"] = {
                "append_daily": {
                    "ok": False,
                    "error": "IBKR connection refused on 127.0.0.1:4002: is IB Gateway running and logged in?",
                }
            }
        elif n == 2:
            doc.update(ok=True, should_rebalance=True, steps={
                **steps,
                "build_basket": {"ok": True, "orders_rows": 14, "portfolio_value": 1_046_210.55,
                                 "buy_dollars": 61_480.12, "sell_dollars": 58_903.77},
                "submit_orders": {"ok": True, "submitted": True, "result": {"submitted": 14}},
            }, rebalance_decision={"cadence": "drift", "rebalance": True,
                                   "reason": "max drift 0.0612 exceeds band 0.0500"})  # fmt: skip
        elif n < 2:
            reason = f"holdings outside the model: {', '.join(OUTSIDE)}, but in cooldown ({n + 1}d remaining)"
            doc.update(ok=True, should_rebalance=False, steps=steps,
                       rebalance_decision={"cadence": "drift", "rebalance": False, "reason": reason})  # fmt: skip
        else:
            reason = f"max drift {0.018 + 0.003 * n:.4f} within band 0.0500 — skip"
            doc.update(ok=True, should_rebalance=False, steps=steps,
                       rebalance_decision={"cadence": "drift", "rebalance": False, "reason": reason})  # fmt: skip
        path = folder / f"daily_run_{run_id}.json"
        path.write_text(json.dumps(doc, indent=2))
        finished = started.astimezone().timestamp() + 154 + 9 * n
        os.utime(path, (finished, finished))


# ── the doctor ─────────────────────────────────────────────────────────


def doctor_report(broker: StagingBroker, ran: datetime, m: Moment | None = None) -> dict[str, Any]:
    """The doctor's report on the staging account, in the real doctor's words
    (trading-engine/src/broker/ibkr/doctor.py)."""
    from src.broker.ibkr import doctor

    m = m or Moment.at(ran)
    ep = broker.endpoint
    snap = broker.snapshot()
    spy = next(p for p in snap.positions if p.symbol == "SPY")
    gross = sum(abs(p.market_value) for p in snap.positions)
    shown = ", ".join(f"{p.symbol} {p.quantity:g}" for p in snap.positions[:8])
    spy_close = spy.last if m.complete else BY_HOLDING["SPY"].prev_close  # the last completed session's
    fix_quotes = (
        "Paper accounts only get real-time quotes shared from the live login: Client Portal (live user) → "
        "Settings → Paper Trading Account → share market data, and hold the US securities snapshot/streaming "
        "subscription."
    )
    money = doctor._fmt_money
    rows = [
        doctor.check_config(ep),
        doctor.CheckResult("socket", doctor.PASS, f"{ep.port} listening (IB Gateway paper)"),
        doctor.CheckResult("handshake", doctor.PASS, f"API server version 178, clientId {ep.client_id('doctor')}"),
        doctor.CheckResult("account", doctor.PASS, f"{ACCOUNT} (paper)"),
        doctor.CheckResult(
            "balances",
            doctor.PASS,
            f"NLV {money(snap.nav)} USD, cash {money(snap.cash)}, gross positions {money(gross)}, "
            f"buying power {money(snap.nav * 2.62)}",
        ),  # fmt: skip
        doctor.CheckResult(
            "positions",
            doctor.PASS,
            f"{len(snap.positions)} positions (0 short): {shown}, +{len(snap.positions) - 8} more",
        ),
        doctor.CheckResult(
            "orders",
            doctor.PASS,
            "0 open orders, 0 executions today (the API only returns today's fills; history needs a Flex query)",
        ),  # fmt: skip
        doctor.CheckResult("clock", doctor.PASS, f"server {ran:%Y-%m-%d %H:%M:%S} UTC, local clock off by 0s"),
        doctor.CheckResult("contracts", doctor.PASS, f"all {len(MODEL)} symbols qualified"),
        doctor.CheckResult("history", doctor.PASS, f"SPY 5 daily bars, last {m.last_close} close {spy_close}"),
        doctor.CheckResult("quotes", doctor.WARN, f"SPY {spy.last:.2f}, delayed 15 min", fix_quotes),
        doctor.CheckResult(
            "order preview", doctor.PASS, f"BUY 1 SPY: accepted, margin change {spy.last * 0.25:,.2f}, commission 1.00"
        ),
        doctor.CheckResult("short preview", doctor.SKIP, "account is long SPY; a 1-share sell is not a short"),
    ]
    return {
        "ran_at": ran.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "profile": "paper",
        "endpoint": f"{ep.host}:{ep.port}",
        "results": [{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix or ""} for r in rows],
    }


# ── wiring ─────────────────────────────────────────────────────────────


class Patches:
    """setattr / setenv / delenv with undo: pytest's MonkeyPatch, for a process without pytest."""

    def __init__(self) -> None:
        self._undo: list[Callable[[], None]] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        old = getattr(target, name)
        self._undo.append(lambda: setattr(target, name, old))
        setattr(target, name, value)

    def setenv(self, name: str, value: str) -> None:
        self._restore_env(name)
        os.environ[name] = value

    def delenv(self, name: str, raising: bool = True) -> None:
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        self._restore_env(name)
        del os.environ[name]

    def _restore_env(self, name: str) -> None:
        old = os.environ.get(name)
        self._undo.append(lambda: os.environ.__setitem__(name, old) if old is not None else os.environ.pop(name, None))

    def undo(self) -> None:
        while self._undo:
            self._undo.pop()()


# settings that would make staging read or reach something real, or differ by machine
_UNSET = (
    "DV02_INCEPTION_DATE", "DV02_INCEPTION_NAV", "DV02_ENGINE_PYTHON", "DV02_RUNNER_TIME", "DV02_PROFILE",
    "CAPITALFUND_OPTIMIZER",
)  # fmt: skip


def isolate(root: Path, clock: Callable[[], datetime], patch: Any) -> None:
    """Point the API and the engine's kill switch and basket at ``root``, never read .env, run on ``clock``."""
    from src.broker.ibkr import settings
    from src.execution import kill_switch
    from src.production.runtime import build_paper_basket

    from . import app as app_mod

    patch.setattr(engine, "ENGINE_ROOT", root)
    patch.setattr(kill_switch, "KILL_SWITCH_PATH", root / "artifacts" / "runs" / "kill_switch.json")
    patch.setattr(build_paper_basket, "_project_root", lambda: root)  # the preview's targets and prices
    patch.setattr(settings, "load_dotenv", lambda *a, **k: None)
    for var in [*_UNSET, *(v for v in os.environ if v.startswith("IBKR_"))]:
        patch.delenv(var, raising=False)
    patch.setattr(app_mod, "_now", clock)


def stub_side_effects(scenario: str, broker: StagingBroker, clock: Callable[[], datetime], patch: Any) -> None:
    """The doctor reports on the staging account; Submit refuses, never starting the daily runner;
    no LaunchAgent is read (fresh has none installed, history has one)."""
    from . import app as app_mod
    from . import ops, runs

    def refuse(profile: str) -> bool:
        raise app_mod.Problem(
            409,
            "Staging never starts the daily runner",
            "This API serves a made-up copy of the paper account; nothing is sent to IBKR.",
        )

    async def run_doctor(profile: str) -> dict[str, Any]:
        report = doctor_report(broker, clock())
        (engine.api_state_dir() / f"doctor_{profile}.json").write_text(json.dumps(report, indent=2))
        return report

    not_run = ops.not_run_report

    patch.setattr(runs, "launch_agent", lambda: None)
    patch.setattr(runs, "launch_agent_installed", lambda: scenario == "history")
    patch.setattr(runs, "start_daily_run", refuse)
    patch.setattr(ops, "run_doctor", run_doctor)
    patch.setattr(ops, "not_run_report", lambda profile: {**not_run(profile), "ran_at": app_mod._iso(clock())})


def create_staging_app(scenario: str, root: Path, now: datetime | None, patch: Any) -> Any:
    """The real app (create_app) over the staging broker and a staging engine tree at ``root``.
    ``patch`` is a Patches or pytest's monkeypatch: undo it to restore the real paths."""
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {', '.join(SCENARIOS)}, got {scenario!r}")
    clock = Clock(now)
    start = clock()
    if Moment.at(start).prev <= FRESH_MASTER_END:
        raise ValueError(f"--now must be after {FRESH_MASTER_END}, where the fresh scenario's price master ends")
    isolate(root, clock, patch)
    build_tree(root, scenario, start)
    broker = StagingBroker(clock, sample_from=start if scenario == "fresh" else None)
    stub_side_effects(scenario, broker, clock, patch)
    from .app import create_app

    app = create_app("paper", broker=broker, background=False)  # no gateway, Flex or doctor upkeep
    app.state.staging_clock = clock
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m dv02_api.staging", description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", choices=SCENARIOS, default="fresh")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument(
        "--now", type=parse_now, default=None, help="start the clock here (ISO 8601; ET without offset)"
    )
    args = parser.parse_args(argv)

    pin_timezone()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    # uvicorn re-raises SIGTERM once it has shut down; leave through `finally`, which deletes the files
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    folder = Path(tempfile.mkdtemp(prefix=f"dv02-staging-{args.scenario}-"))
    try:
        app = create_staging_app(args.scenario, folder / "trading-engine", args.now, Patches())
        clock = app.state.staging_clock
        print(
            f"DV02 staging API, {args.scenario} scenario: http://127.0.0.1:{args.port}/api/v1/status\n"
            f"  clock starts {clock():%Y-%m-%d %H:%M:%S} UTC; engine files in {folder} (deleted on exit)",
            flush=True,
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port, loop="asyncio", log_level="warning")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
