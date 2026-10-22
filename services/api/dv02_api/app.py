"""FastAPI app implementing schemas/api/v1.yaml over real data."""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers

from . import (
    engine,
    flex,
    history,
    mandate,
    nyse,
    ops,
    orders,
    performance,
    rebalance,
    risk,
    runs,
    stats,
    strategies,
    stream,
)
from .broker import PER_CONTRACT, AccountSnap, Broker, PositionSnap, lookup_position
from .universe import BENCHMARKS, BY_SYMBOL

log = logging.getLogger(__name__)

DEFAULT_BETA = {
    "equity": 1.0, "fixed_income": 0.0, "cash": 0.0, "commodity": 0.2,
    "crypto": 1.0, "volatility": -3.0, "index_future": 1.0,
}  # fmt: skip
BAR_SYMBOLS = ["SPY", "QQQ", "IWM", "ACWI", "AGG", "BIL"]
MANDATES = {
    "Hedge": "Futures overlay against the book's market beta",
}
ETF_MANDATE = "Long-only ETF rotation on 126-day momentum; rebalances when weights drift more than 5%"


class Problem(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status, self.title, self.detail = status, title, detail


def problem_response(exc: Problem) -> JSONResponse:
    body = {"title": exc.title}
    if exc.detail:
        body["detail"] = exc.detail
    return JSONResponse(body, status_code=exc.status, media_type="application/problem+json")


@dataclass
class Ctx:
    profile: str
    broker: Any
    beta_cache: dict[tuple[str, date | None], float] = field(default_factory=dict)
    orders_refreshed: float = 0.0
    targets_cache: tuple[float, dict[str, float], date | None] | None = None
    sleeve_cache: tuple[float, str] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ── shared building blocks ─────────────────────────────────────────────


def snapshot(ctx: Ctx) -> AccountSnap | None:
    """Live account from IBKR, else the engine's last saved snapshot."""
    snap = ctx.broker.snapshot()
    return snap if snap is not None else offline_snapshot(ctx.profile)


def offline_snapshot(profile: str) -> AccountSnap | None:
    tags_at = history.latest_snapshot_tags(profile)
    pos_at = history.positions_snapshot(profile)
    if tags_at is None or pos_at is None:
        return None
    tags, tags_time = tags_at
    rows, pos_time = pos_at
    try:
        nav = float(tags["NetLiquidation"])
    except (KeyError, ValueError):
        return None
    priced_on = nyse.session_state(pos_time).trading_date
    positions = []
    for r in rows:
        # files written before the snapshot had sec_type, multiplier and market_price: shares, as before
        sec_type = r["sec_type"] or "STK"
        if sec_type == "STK" and r["symbol"] in BY_SYMBOL:
            sec_type = "ETF"
        mult = r["multiplier"] or 1.0
        # IBKR's mark when the snapshot was taken, the same moment as its NAV; else the last close
        closes = history.closes(r["symbol"])
        last, day = r["market_price"], priced_on
        if last is None and closes:
            day, last = closes[-1]
        if r["multiplier"] is None and sec_type in PER_CONTRACT:
            # the engine saves an unknown contract size empty: valued at 1x, MES would count a fifth of
            # its notional, so it gets no price and the no-leverage check blocks it, as the runner's does
            last = None
        prev = next((c for d, c in reversed(closes) if d < day), last)
        unit = r["quantity"] * mult
        avg_cost = r["avg_cost"] / (mult if sec_type in PER_CONTRACT else 1.0)  # IBKR's is per contract
        positions.append(
            PositionSnap(
                con_id=0, symbol=r["symbol"], root=_root(r["symbol"], sec_type), sec_type=sec_type,
                quantity=r["quantity"], multiplier=mult, avg_cost=avg_cost, last=last, prev_close=prev,
                # no price at all: no value either, so the no-leverage check blocks it as unpriced
                market_value=unit * last if last is not None else 0.0,
                unrealized_pnl=unit * (last - avg_cost) if last is not None else 0.0, day_pnl=None,
            )
        )  # fmt: skip
    try:
        cash = float(tags.get("TotalCashValue", "0"))
    except ValueError:
        cash = 0.0
    return AccountSnap(
        account="offline", currency="USD", nav=nav, cash=cash, day_pnl=None,
        as_of=min(tags_time, pos_time).astimezone(timezone.utc), positions=positions, source="snapshot file",
    )  # fmt: skip


_EXPIRY = re.compile(r"(?P<root>[A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,2}")  # MESZ6: root, month code, year


def _root(symbol: str, sec_type: str) -> str:
    """The snapshot keys a future or option by its local symbol (MESZ6, "SPY   261218C00600000");
    the instrument table knows the root (MES, SPY)."""
    root = symbol.split()[0] if symbol.split() else symbol
    if sec_type in ("FUT", "FOP") and (m := _EXPIRY.fullmatch(root)):
        return m["root"]
    return root


def require_snapshot(ctx: Ctx) -> AccountSnap:
    snap = snapshot(ctx)
    if snap is None:
        raise Problem(
            503,
            "IBKR not connected",
            f"{ctx.broker.status.error}. No saved account snapshot to fall back on yet either.",
        )
    return snap


def returns_of(symbol: str) -> dict[date, float]:
    return risk.returns_from_closes(history.closes(symbol))


def beta(ctx: Ctx, p: PositionSnap) -> float:
    inst = lookup_position(p)
    fallback = DEFAULT_BETA.get(inst.asset_class, 1.0) * inst.leverage
    if p.sec_type == "FUT":
        return fallback
    spy = history.closes("SPY")
    key = (p.symbol, spy[-1][0] if spy else None)
    if key not in ctx.beta_cache:
        ctx.beta_cache[key] = risk.beta_of(returns_of(p.symbol), risk.returns_from_closes(spy), fallback)
    return ctx.beta_cache[key]


def targets(ctx: Ctx) -> tuple[dict[str, float], date | None]:
    path = engine.data_dir() / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}, None
    if ctx.targets_cache is None or ctx.targets_cache[0] != mtime:
        from src.production.runtime.build_paper_basket import load_latest_targets

        df = load_latest_targets(path)
        latest = df["date"].max()
        df = df[df["date"] == latest]
        ctx.targets_cache = (
            mtime,
            {str(s): float(w) for s, w in zip(df["symbol"], df["target_weight"], strict=False)},
            latest.date() if hasattr(latest, "date") else None,
        )
    return ctx.targets_cache[1], ctx.targets_cache[2]


def sleeve(ctx: Ctx) -> str:
    if ctx.sleeve_cache is None or time.monotonic() - ctx.sleeve_cache[0] > 60:
        ctx.sleeve_cache = (time.monotonic(), rebalance.sleeve_name())
    return ctx.sleeve_cache[1]


def contract_sec_type(sec_type: str) -> str:
    """The contract's Position.sec_type is STK, ETF, FUT or OPT. A futures option or a warrant
    shows as OPT; FX, a bond or crypto as STK, with an asset_class that says what it is."""
    if sec_type in ("STK", "ETF", "FUT", "OPT"):
        return sec_type
    return {"FOP": "OPT", "WAR": "OPT", "CONTFUT": "FUT"}.get(sec_type, "STK")


def position_rows(ctx: Ctx, snap: AccountSnap) -> list[dict[str, Any]]:
    tw, _ = targets(ctx)
    etf_sleeve = sleeve(ctx)
    out = []
    for p in snap.positions:
        inst = lookup_position(p)
        unit = p.quantity * p.multiplier
        last = p.last if p.last is not None else (p.market_value / unit if unit else 0.0)
        prev = p.prev_close if p.prev_close else last
        if not p.prev_close and p.day_pnl is not None and unit and last:
            # no previous close quoted (delayed data off hours): IBKR's day P&L still says how far it moved
            prev = last - p.day_pnl / unit
        day_pnl = p.day_pnl if p.day_pnl is not None else unit * (last - prev)
        cost = p.avg_cost * unit
        out.append(
            {
                "symbol": p.symbol,
                "name": inst.name if inst.name != inst.symbol else p.symbol,
                "asset_class": inst.asset_class,
                "sleeve": "Hedge" if p.sec_type == "FUT" else etf_sleeve,
                "sec_type": contract_sec_type(p.sec_type),
                "quantity": p.quantity,
                "multiplier": p.multiplier,
                "avg_cost": p.avg_cost,
                "last": last,
                "prev_close": prev,
                "market_value": p.market_value,
                "weight": p.market_value / snap.nav if snap.nav else 0.0,
                "target_weight": tw.get(p.symbol) if p.sec_type != "FUT" else None,
                "day_pnl": day_pnl,
                "day_return": last / prev - 1 if prev else 0.0,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_return": p.unrealized_pnl / abs(cost) if cost else 0.0,
                "beta": beta(ctx, p),
            }
        )
    return out


def nav_series(ctx: Ctx, snap: AccountSnap | None, now: datetime) -> history.NavHistory:
    """NAV history plus today's NAV: live from IBKR, else the saved snapshot's."""
    live = None
    if snap is not None:
        when = now if snap.source == "ibkr" else snap.as_of
        live = (nyse.session_state(when).trading_date, snap.nav)
    return history.nav_history(ctx.profile, live)


# ── who may call ───────────────────────────────────────────────────────
# There is no login: the API listens on 127.0.0.1 only. Any web page open in the same
# browser can still send it requests, so every request is checked before it reaches a route:
#  - Host: a hostile domain that resolves to 127.0.0.1 (DNS rebinding) arrives under its own
#    name. Only loopback names, DV02_ALLOWED_HOSTS and the address the connection itself
#    arrived on (an IP, which rebinding can't produce) are served.
#  - Origin, on the WebSocket and on every request that changes something: browsers don't
#    apply CORS to WebSockets, and a form or a no-cors fetch can POST to another site without
#    asking. Pages may call only from the dashboard, a page served from the host and port
#    the request went to (the API's own, or a proxy in front of it), or DV02_ALLOWED_ORIGINS.
#    No Origin at all is a script (curl, Python, bin/tradeit).
#  - Content-Type: a body must be JSON, which another site's page can't send without asking
#    first. A POST with no body (cancel, run the doctor) needs none.

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# the dashboard's dev server and the API itself. The dev server proxies /api with the
# browser's own Host and Origin (changeOrigin: false), so through it both say localhost:5173.
DASHBOARD_ORIGINS = tuple(
    f"{scheme}://{host}:{port}"
    for scheme in ("http", "https")
    for host in ("localhost", "127.0.0.1")
    for port in (5173, 8000)
)
STATE_CHANGING = ("POST", "PUT", "PATCH", "DELETE")
_HOST = re.compile(r"(?:\[(?P<ip6>[0-9a-f:.]+)\]|(?P<name>[^\s:/@\[\]]+))(?::\d{1,5})?")


def host_name(value: str) -> str:
    """A Host header's name: localhost:5173 is localhost, [::1]:8000 is ::1. '' when it isn't one."""
    m = _HOST.fullmatch(value.strip().lower())
    return (m["ip6"] or m["name"]) if m else ""


def _listed(var: str) -> list[str]:
    return [x.strip() for x in os.environ.get(var, "").split(",") if x.strip()]


def _has_body(headers: Headers) -> bool:
    if "transfer-encoding" in headers:
        return True
    try:
        return int(headers.get("content-length") or 0) > 0
    except ValueError:
        return True


@dataclass
class Guard:
    """ASGI middleware: the Host, Origin and Content-Type checks above. A refused HTTP request
    gets a Problem; a refused WebSocket is closed with 1008 (policy violation) before the
    handshake completes, which the server answers with 403."""

    app: Any
    hosts: frozenset[str]
    origins: frozenset[str]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            refused = self.refusal(scope)
            if refused is not None:
                log.warning("refused %s %s: %s", scope.get("method", "WebSocket"), scope["path"], refused.detail)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008, "reason": refused.title})
                else:
                    await problem_response(refused)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def refusal(self, scope: dict[str, Any]) -> Problem | None:
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        name = host_name(host)
        arrived_on = str((scope.get("server") or ("",))[0]).lower()  # the socket's own address
        if not name or (name not in self.hosts and name != arrived_on):
            return Problem(
                400,
                "Host not allowed",
                f"This API doesn't answer to {host or 'a request without a Host header'}. If you reach it "
                "by that name on purpose, add the name to DV02_ALLOWED_HOSTS.",
            )
        websocket = scope["type"] == "websocket"
        if not websocket and scope["method"] not in STATE_CHANGING:
            return None
        origin = headers.get("origin")
        if origin is not None and not self.origin_allowed(origin, host):
            return Problem(
                403,
                "Origin not allowed",
                f"Pages on {origin} can't use this API. If that page is yours, add it to DV02_ALLOWED_ORIGINS.",
            )
        ctype = headers.get("content-type")
        if not websocket and (ctype is not None or _has_body(headers)):
            if (ctype or "").split(";")[0].strip().lower() != "application/json":
                return Problem(415, "Send JSON", f"Content-Type must be application/json, not {ctype or 'none'}.")
        return None

    def origin_allowed(self, origin: str, host: str) -> bool:
        origin = origin.strip().lower().rstrip("/")
        if origin in self.origins:
            return True
        # a page this API (or the dev server in front of it) served: same host and port as the
        # request, and the Host already passed the check above
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc == host.strip().lower()


def guard_settings() -> dict[str, frozenset[str]]:
    from src.broker.ibkr import settings  # trading-engine: loads .env

    settings.load_dotenv()
    hosts = [*LOOPBACK_HOSTS, *(host_name(h) or h.strip("[]").lower() for h in _listed("DV02_ALLOWED_HOSTS"))]
    origins = [*DASHBOARD_ORIGINS, *(o.lower().rstrip("/") for o in _listed("DV02_ALLOWED_ORIGINS"))]
    return {"hosts": frozenset(h for h in hosts if h), "origins": frozenset(origins)}


async def json_body(request: Request) -> Any:
    """The request's JSON, or None when it has no body or the body isn't JSON (Guard has
    already refused a body of any other Content-Type)."""
    try:
        return await request.json()
    except ValueError:
        return None


# ── app ────────────────────────────────────────────────────────────────


def create_app(profile: str | None = None, broker: Any = None, background: bool = True) -> FastAPI:
    profile = (profile or os.environ.get("DV02_PROFILE", "paper")).lower()
    if profile not in ("paper", "live"):
        raise ValueError(f"profile must be paper or live, got {profile!r}")
    ctx = Ctx(profile=profile, broker=broker if broker is not None else Broker(profile))
    hub = stream.Hub(clock=lambda: _iso(_now()))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks: list[asyncio.Task] = []
        if background:
            await ctx.broker.start()
            tasks = [asyncio.create_task(_maintenance(ctx), name="maintenance")]
        try:
            yield
        finally:
            await hub.stop()
            for t in tasks:
                t.cancel()
            if background:
                await ctx.broker.stop()

    app = FastAPI(title="DV02 Fund API", version="1.0.0", lifespan=lifespan)
    app.state.ctx = ctx
    app.state.stream = hub
    app.add_middleware(Guard, **guard_settings())

    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem) -> JSONResponse:
        return problem_response(exc)

    @app.exception_handler(FileNotFoundError)
    async def _missing(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return problem_response(
            Problem(503, "Engine data not found", f"{exc}. Run the daily runner once to create it.")
        )

    v1 = "/api/v1"

    # ── status ────────────────────────────────────────────────────────
    @app.get(f"{v1}/status")
    async def get_status() -> dict[str, Any]:
        now = _now()
        s = nyse.session_state(now)
        b = ctx.broker
        st = b.status
        snap_offline = None if b.connected else offline_snapshot(ctx.profile)
        return {
            "mode": ctx.profile,
            "account": {"id": st.account or (b.endpoint.account or "not connected"), "base_currency": "USD"},
            "as_of": _iso(now),
            "market": {
                "session": s.session,
                "trading_date": s.trading_date.isoformat(),
                "next_change_at": _iso(s.next_change_at),
                "next_change": s.next_change,
            },
            "components": components(ctx, snap_offline),
            "kill_switch": ops.kill_switch(),
            "schedule": runs.schedule(now),
        }

    @app.get(f"{v1}/market/strip")
    async def get_strip() -> dict[str, Any]:
        quotes = ctx.broker.quotes() if ctx.broker.connected else []
        return {
            "as_of": _iso(_now()),
            "delayed": any(q["_type"] in (3, 4) for q in quotes),
            "items": [{k: v for k, v in q.items() if not k.startswith("_")} for q in quotes],
        }

    # ── portfolio ─────────────────────────────────────────────────────
    @app.get(f"{v1}/portfolio/summary")
    async def get_summary() -> dict[str, Any]:
        now = _now()
        snap = require_snapshot(ctx)
        rows = position_rows(ctx, snap)
        h = nav_series(ctx, snap, now)
        navs = h.navs
        start = navs[0][0]
        day_pnl = snap.day_pnl if snap.day_pnl is not None else sum(r["day_pnl"] for r in rows)
        last = navs[-1][0]
        month_base = date(last.year, last.month, 1) - timedelta(days=1)
        year_base = date(last.year - 1, 12, 31)
        itd = navs[-1][1] / navs[0][1] - 1 if len(navs) >= 2 else None
        spy = history.closes("SPY")
        spy_base = next((c for d, c in reversed(spy) if d <= start), None)
        rets = stats.returns_of([v for _, v in navs])
        long_ = sum(r["weight"] for r in rows if r["weight"] > 0)
        short = sum(r["weight"] for r in rows if r["weight"] < 0)
        return {
            "as_of": _iso(snap.as_of),
            "inception_date": start.isoformat(),
            "nav": snap.nav,
            "cash": snap.cash,
            "day_pnl": day_pnl,
            "day_return": day_pnl / (snap.nav - day_pnl) if snap.nav - day_pnl else 0.0,
            "mtd_return": performance.period_return(navs, month_base, h.complete),
            "ytd_return": performance.period_return(navs, year_base, h.complete),
            "itd_return": itd,
            "benchmark": BENCHMARKS[0],
            "benchmark_itd_return": spy[-1][1] / spy_base - 1 if itd is not None and spy and spy_base else None,
            "long_exposure": long_,
            "short_exposure": short,
            "gross_exposure": long_ - short,
            "net_exposure": long_ + short,
            "beta": sum(r["weight"] * r["beta"] for r in rows),
            "sharpe_itd": stats.sharpe(rets) if len(rets) >= stats.MIN_RATIO_OBS else None,
            "positions_count": len(rows),
            "nav_history": [{"date": d.isoformat(), "value": v} for d, v in navs[-30:]],
            "history": h.coverage(),
        }

    @app.get(f"{v1}/portfolio/positions")
    async def get_positions() -> dict[str, Any]:
        snap = require_snapshot(ctx)
        return {"as_of": _iso(snap.as_of), "nav": snap.nav, "positions": position_rows(ctx, snap)}

    @app.get(f"{v1}/portfolio/intraday")
    async def get_intraday() -> dict[str, Any]:
        now = _now()
        s = nyse.session_state(now)
        d = s.trading_date
        points = ctx.broker.intraday(d) if hasattr(ctx.broker, "intraday") else []
        live = ctx.broker.snapshot() if s.session == "open" else None
        if live is not None and live.day_pnl is not None:
            # the chart's tip is today's P&L now, so it moves between the samples
            points = [(t, v) for t, v in points if t < now.replace(microsecond=0)] + [(now, live.day_pnl)]
        return {
            "trading_date": d.isoformat(),
            "session_open": _iso(nyse.et_time(d, nyse.OPEN)),
            "session_close": _iso(nyse.et_time(d, nyse.close_time(d))),
            "live": s.session == "open",
            "points": [{"t": _iso(t), "pnl": v} for t, v in points],
        }

    # ── performance ───────────────────────────────────────────────────
    @app.get(f"{v1}/benchmarks")
    async def get_benchmarks() -> list[dict[str, Any]]:
        return BENCHMARKS

    @app.get(f"{v1}/performance")
    async def get_performance(
        range_: str = Query("ITD", alias="range"), benchmark: str = Query("SPY")
    ) -> dict[str, Any]:
        if range_ not in ("1W", "MTD", "1M", "3M", "6M", "YTD", "1Y", "ITD"):
            raise Problem(400, "Unknown range", range_)
        bench = next((b for b in BENCHMARKS if b["id"] == benchmark), None)
        if bench is None:
            raise Problem(400, "Unknown benchmark", benchmark)
        live = ctx.broker.snapshot()
        h = nav_series(ctx, live if live is not None else offline_snapshot(ctx.profile), _now())
        levels = history.benchmark_levels(benchmark)
        if not levels:
            raise Problem(503, f"No price history for {benchmark}", "Start IB Gateway so the API can load daily bars.")
        try:
            return performance.performance(h.navs, levels, range_, bench)
        except ValueError:
            raise Problem(
                503,
                "Not enough NAV history yet",
                f"{len(h.navs)} day(s) so far ({', '.join(h.sources) or 'none'}). Run bin/tradeit flex once to load "
                "your daily NAV since the account opened; the API also records each close from now on.",
            ) from None

    # ── risk ──────────────────────────────────────────────────────────
    @app.get(f"{v1}/risk")
    async def get_risk() -> dict[str, Any]:
        now = _now()
        snap = require_snapshot(ctx)
        rows = position_rows(ctx, snap)
        navs = nav_series(ctx, snap, now).navs
        peak = max(v for _, v in navs)
        weights: dict[str, float] = {}
        for r in rows:
            weights[r["symbol"]] = weights.get(r["symbol"], 0.0) + r["weight"]
        # futures are proxied by the index they track
        rets = {r["symbol"]: returns_of("SPY" if r["sec_type"] == "FUT" else r["symbol"]) for r in rows}
        sleeves: dict[str, dict[str, Any]] = {}
        for r in rows:
            sl = sleeves.setdefault(
                r["sleeve"], {"sleeve": r["sleeve"], "gross": 0.0, "net": 0.0, "day_pnl": 0.0,
                              "mandate": MANDATES.get(r["sleeve"], ETF_MANDATE)},
            )  # fmt: skip
            sl["gross"] += abs(r["weight"])
            sl["net"] += r["weight"]
            sl["day_pnl"] += r["day_pnl"]
        day_pnl = snap.day_pnl if snap.day_pnl is not None else sum(r["day_pnl"] for r in rows)
        cfg = {
            "gross": mandate.POLICY["max_gross"],
            "cash": mandate.POLICY["min_cash"],
            "net": mandate.POLICY["max_net"],
            "single_name": mandate.POLICY["max_single_name"],
        }
        # gross and cash as the order gate measures them: every holding, futures at notional
        try:
            book, engine_error = mandate.exposure(snap), ""
        except mandate.EngineMissing as exc:
            book, engine_error = None, str(exc)
        doc = risk.report(
            as_of=_iso(snap.as_of),
            nav=snap.nav,
            positions=rows,
            history_rets=risk.portfolio_history(weights, rets),
            beta=sum(r["weight"] * r["beta"] for r in rows),
            drawdown=snap.nav / peak - 1 if peak else 0.0,
            day_return=day_pnl / snap.nav if snap.nav else 0.0,
            sleeves=list(sleeves.values()),
            limits_cfg=cfg,
            durations={s: i.duration for s, i in BY_SYMBOL.items()},
            cash=snap.cash,
            book=book,
            engine_error=engine_error,
        )
        for lim in doc["limits"]:
            note = risk.ENFORCEMENT.get(lim["id"], risk.NOT_ENFORCED)
            lim["detail"] = f"{lim['detail']} · {note}" if lim["detail"] else note
        return doc

    # ── trading ───────────────────────────────────────────────────────
    @app.get(f"{v1}/trading/orders")
    async def get_orders() -> dict[str, Any]:
        b = ctx.broker
        if b.connected and time.monotonic() - ctx.orders_refreshed > 15:
            ctx.orders_refreshed = time.monotonic()
            await b.refresh_orders()
        live = [orders.from_trade(t, sleeve(ctx)) for t in b.trades()] if b.connected else []
        s = nyse.session_state(_now())
        if live:
            return {"trading_date": nyse.et_date(_now()).isoformat(), "orders": live}
        day, saved = orders.from_files(ctx.profile, sleeve(ctx))
        if saved and not b.connected:
            return {"trading_date": day or s.trading_date.isoformat(), "orders": saved}
        return {"trading_date": s.trading_date.isoformat(), "orders": []}

    @app.post(f"{v1}/trading/orders/{{order_id}}/cancel")
    async def cancel_order(order_id: str) -> dict[str, Any]:
        b = ctx.broker
        if not b.connected:
            raise Problem(409, "IBKR not connected", b.status.error)
        trade = next((t for t in b.trades() if orders.order_id(t) == order_id), None)
        if trade is None:
            raise Problem(409, "Order not found", f"{order_id} is not an order IBKR reports for this session")
        mapped = orders.from_trade(trade, sleeve(ctx))
        if mapped["status"] in ("filled", "cancelled", "rejected"):
            raise Problem(409, "Order is not cancellable", f"it is {mapped['status']}")
        try:
            await b.cancel(trade)
        except Exception as exc:  # noqa: BLE001
            raise Problem(409, "Cancel failed", str(exc)) from None
        for _ in range(10):
            mapped = orders.from_trade(trade, sleeve(ctx))
            if mapped["status"] == "cancelled":
                break
            await asyncio.sleep(0.3)
        return mapped

    @app.get(f"{v1}/trading/rebalance/preview")
    async def get_preview() -> dict[str, Any]:
        live = ctx.broker.snapshot()
        snap = live if live is not None else offline_snapshot(ctx.profile)
        return await asyncio.to_thread(rebalance.preview, snap, live is not None, ctx.broker.status.error, _now())

    @app.post(f"{v1}/trading/rebalance")
    async def submit_rebalance(request: Request) -> dict[str, Any]:
        body = await json_body(request)
        if not isinstance(body, dict) or body.get("confirm") != "SUBMIT":
            raise Problem(409, "Confirmation required", 'Send {"confirm": "SUBMIT"}')
        if ctx.profile != "paper":
            raise Problem(409, "Live submission is disabled", "The engine's order path is paper-only for now.")
        snap = ctx.broker.snapshot()
        doc = await asyncio.to_thread(rebalance.preview, snap, snap is not None, ctx.broker.status.error, _now())
        reasons = rebalance.blocking(doc)
        if reasons or not doc["can_submit"]:
            raise Problem(409, "Blocked by a pre-trade check", "; ".join(reasons) or "nothing to trade")
        if not runs.start_daily_run(ctx.profile):
            raise Problem(409, "A daily run is already in progress")
        n = doc["totals"]["orders"]
        return {
            "submitted": 0,
            "orders": [],
            "message": (
                f"Daily run started. The engine refreshes bars and targets, rebuilds the basket from live "
                f"positions and, if its risk gate passes, sends about {n} orders. They appear in the blotter "
                "as IBKR accepts them."
            ),
        }

    # ── ops ───────────────────────────────────────────────────────────
    @app.get(f"{v1}/ops/doctor")
    async def get_doctor() -> dict[str, Any]:
        return ops.last_doctor(ctx.profile) or ops.not_run_report(ctx.profile)

    @app.post(f"{v1}/ops/doctor/run")
    async def post_doctor() -> dict[str, Any]:
        return await ops.run_doctor(ctx.profile)

    @app.post(f"{v1}/ops/kill-switch")
    async def post_kill_switch(request: Request) -> dict[str, Any]:
        body = await json_body(request)
        if not isinstance(body, dict):  # never disarm on a request that doesn't say so
            raise Problem(400, "Send a JSON object", 'For example {"armed": true, "reason": "..."}')
        armed, reason = body.get("armed"), body.get("reason")
        # {} or {"armed": "false"} says nothing: only a JSON true or false arms or disarms
        if not isinstance(armed, bool) or not isinstance(reason, (str, type(None))):
            raise Problem(
                400,
                "Say true or false for armed",
                'Send {"armed": true, "reason": "..."} to arm, or {"armed": false} to disarm; reason is text',
            )
        reason = (reason or "").strip()
        if armed and not reason:
            raise Problem(400, "A reason is required to arm the kill switch")
        state = ops.set_kill_switch(armed, reason, by=f"dashboard ({getpass.getuser()})")
        if armed:
            # The switch blocks new orders; the ones already at IBKR would still fill, so
            # they are cancelled too, as bin/tradeit kill does. Best effort: the switch is
            # armed whatever happens here, and a failed cancel is logged.
            b = ctx.broker
            for trade in b.trades() if b.connected else []:
                if orders.from_trade(trade, sleeve(ctx))["status"] in ("filled", "cancelled", "rejected"):
                    continue
                try:
                    await b.cancel(trade)
                except Exception as exc:  # noqa: BLE001
                    log.warning("kill switch armed, but cancelling %s failed: %s", orders.order_id(trade), exc)
        return state

    @app.get(f"{v1}/strategies")
    async def get_strategies() -> dict[str, Any]:
        doc = await asyncio.to_thread(strategies.load)
        return {"as_of": _now().isoformat(timespec="seconds"), **doc}

    @app.get(f"{v1}/data/datasets")
    async def get_datasets() -> dict[str, Any]:
        from src.data import datasets  # trading-engine

        now = _now()
        # off the event loop: the files and, when it is on, the store can be slow
        rows = await asyncio.to_thread(datasets.status, now, engine.ENGINE_ROOT, ctx.profile)
        last = datasets.last_catchup(engine.ENGINE_ROOT, ctx.profile)
        return {
            "as_of": now.isoformat(timespec="seconds"),
            "datasets": [s.as_dict() for s in rows],
            "last_catchup": {"at": last["at"], "actions": last["actions"]} if last else None,
        }

    @app.get(f"{v1}/ops/exit-test")
    async def get_exit_test() -> dict[str, Any]:
        from src.runtime import exit_test  # trading-engine

        results = exit_test.evaluate(ctx.profile, today=nyse.et_date(_now()), root=engine.ENGINE_ROOT)
        return exit_test.report(results, ctx.profile, exit_test.NAV_TOLERANCE)

    @app.get(f"{v1}/ops/engine-shadow")
    async def get_engine_shadow() -> dict[str, Any]:
        from src.production import engine_shadow  # trading-engine

        return await asyncio.to_thread(engine_shadow.status, engine.ENGINE_ROOT)

    @app.get(f"{v1}/ops/runs")
    async def get_runs() -> dict[str, Any]:
        out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in runs.load_runs(30)]
        doc = ops.last_doctor(ctx.profile)
        if doc:
            res = doc["results"]
            counts = {s: sum(r["status"] == s for r in res) for s in ("PASS", "WARN", "FAIL")}
            out.append(
                {
                    "id": f"doctor-{doc['ran_at']}",
                    "kind": "doctor",
                    "started_at": doc["ran_at"],
                    "ok": counts["FAIL"] == 0,
                    "summary": f"IBKR doctor: {counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail",
                    "steps": [{"name": r["name"], "ok": r["status"] != "FAIL", "detail": r["detail"]} for r in res],
                }
            )
        out.sort(key=lambda r: r["started_at"], reverse=True)
        return {"runs": out}

    # ── stream ────────────────────────────────────────────────────────
    # the REST routes' own functions, so a frame's data is the REST body byte for byte
    hub.topics = {
        "status": get_status, "strip": get_strip, "summary": get_summary,
        "positions": get_positions, "intraday": get_intraday, "orders": get_orders,
    }  # fmt: skip

    @app.websocket(f"{v1}/stream")
    async def open_stream(ws: WebSocket) -> None:
        await hub.serve(ws)

    dist = os.environ.get("DV02_WEB_DIST")
    if dist:
        serve_dashboard(app, Path(dist))
    return app


def serve_dashboard(app: FastAPI, dist: Path) -> None:
    """Serve the built dashboard (apps/web: npm run build, so apps/web/dist) from
    the API's own port, for a host without the Vite dev server (the VM, behind a
    Cloudflare tunnel). Same origin as the API, so the Host/Origin guard covers
    both. Registered after every API route: /api paths never reach it, and an
    unknown one is still a 404. Any other path that isn't a file in ``dist`` is
    the app's index.html, so the dashboard's own routes load on refresh."""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"DV02_WEB_DIST={dist}: no index.html there; build the dashboard (npm run build)")

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise Problem(404, "Not found", f"/{path} is not an API route")
        file = (root / path).resolve()
        if path and file.is_file() and root in file.parents:
            return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


# ── status components ──────────────────────────────────────────────────


def components(ctx: Ctx, offline: AccountSnap | None) -> list[dict[str, str]]:
    b = ctx.broker
    st = b.status
    if b.connected:
        gateway = {
            "status": "ok",
            "detail": f"{ctx.profile} account {st.account} on {st.endpoint}, client {b.client_id}",
        }
    else:
        extra = f" Showing the snapshot saved {offline.as_of.astimezone():%b %d %H:%M}." if offline else ""
        gateway = {"status": "error", "detail": f"{st.error}.{extra}"}
    mdt = b.market_data_type() if b.connected else None
    if mdt in (1, 2):
        market = {"status": "ok", "detail": "Real-time" if mdt == 1 else "Real-time (frozen after the close)"}
    elif mdt in (3, 4):
        market = {
            "status": "warning",
            "detail": "Delayed 15 min: no real-time subscription on this login. Paper accounts need market data "
            "shared from the live login (Client Portal → Settings → Paper Trading Account)",
        }
    else:
        market = {"status": "unknown", "detail": "no quotes yet" if b.connected else "IBKR not connected"}

    installed = runs.schedule_installed()
    last = next((r for r in runs.load_runs(5) if not r["dry_run"]), None)
    last_text = f"last run {last['started_at'][:16].replace('T', ' ')}: {last['summary']}" if last else "no runs yet"
    if installed is False:
        sched = {
            "status": "warning",
            "detail": f"Not scheduled: {runs.SET_UP_HINT[runs.scheduler()]}",
        }
    elif last is not None and not last["ok"]:
        sched = {"status": "warning", "detail": last_text}
    elif installed is None:
        where = "the Mac's LaunchAgent or the VM's dv02-runner.timer"
        sched = {"status": "unknown", "detail": f"can't tell if the daily run is scheduled here ({where}); {last_text}"}
    else:
        sched = {"status": "ok", "detail": f"Daily run {runs.runner_time():%H:%M} local; {last_text}"}

    flex_note = "IBKR Flex NAV loaded" if flex.read_cache(ctx.profile) else "no Flex NAV yet"
    return [
        {"id": "gateway", "label": "IB Gateway", **gateway},
        {"id": "market_data", "label": "Market data", **market},
        {"id": "engine", "label": "Engine", **engine_health()},
        {"id": "scheduler", "label": "Scheduler", **sched},
        {"id": "database", "label": "Database", **_with(ops.store_health(), flex_note)},
    ]  # fmt: skip


def _with(health: dict[str, str], note: str) -> dict[str, str]:
    return {**health, "detail": f"{health['detail']}; {note}"}


def engine_health() -> dict[str, str]:
    """The C++ engine every order's no-leverage check runs on: it imports, and it still tells
    a levered book from an unlevered one."""
    try:
        import dv02_engine
        from dv02_engine import risk
    except ImportError:
        return {"status": "error", "detail": f"C++ engine not installed in {sys.executable}: run bin/tradeit setup"}
    version = getattr(dv02_engine, "__version__", "unknown version")
    try:
        etf = risk.AssetClass.ETF
        # fully invested in five names at 20% each, then one more share bought on margin: 110% gross, cash -$100
        book = risk.Book(1000.0, 0.0, [risk.Position(s, 2.0, 100.0, asset_class=etf) for s in "ABCDE"])
        fits = risk.check(book, [], risk.Policy())
        levered = risk.check(book, [risk.Order("A", 1.0, 100.0, asset_class=etf)], risk.Policy())
        rules = {b.rule for b in levered.breaches if b.blocking}
        problem = None if fits.ok and not levered.ok and {"gross", "cash"} <= rules else "it let a levered book through"
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {exc}"
    if problem:
        return {
            "status": "error",
            "detail": f"C++ engine dv02_engine {version} failed its self-check ({problem}): rebuild it with "
            "bin/tradeit engine",
        }
    return {"status": "ok", "detail": f"C++ engine dv02_engine {version}; the no-leverage check passed its self-check"}


# ── background upkeep ──────────────────────────────────────────────────


# seconds between upkeep passes
MAINTENANCE_INTERVAL = 60.0


async def _flex_refresh(profile: str) -> None:
    try:
        log.info(await asyncio.to_thread(flex.refresh, profile))
    except Exception as exc:  # noqa: BLE001
        log.warning("Flex refresh failed: %s", exc)


async def _maintenance(ctx: Ctx) -> None:
    """Record closing NAV, keep daily bars and Flex NAV fresh, and run the doctor at
    start when its last report is over 12 hours old (on the VM dv02-doctor.timer
    runs it before every daily run).

    The Flex refresh runs as a task of its own, one at a time: a full history
    walk can take many minutes under IBKR's pacing and retries, and the
    closing NAV must not wait for it."""
    # None until the first pass: due at once (monotonic time counts from boot, so a
    # zero start would wait out the interval on a machine booted less than that ago)
    last_flex: float | None = None
    last_bars: float | None = None
    doctor_started = False
    last_store: float | None = None
    flex_task: asyncio.Task | None = None
    try:
        while True:
            try:
                now = _now()
                b = ctx.broker
                s = nyse.session_state(now)
                snap = b.snapshot() if b.connected else None
                closed = nyse.et_time(s.trading_date, nyse.close_time(s.trading_date)) + nyse.CLOSE_SETTLE
                if snap is not None and s.session != "open" and nyse.et_date(now) == s.trading_date and now >= closed:
                    history.record_close_nav(ctx.profile, s.trading_date, snap.nav)
                if b.connected and (last_bars is None or time.monotonic() - last_bars > 1800):
                    last_bars = time.monotonic()
                    held = [p.symbol for p in (snap.positions if snap else []) if p.sec_type in ("STK", "ETF")]
                    for sym in dict.fromkeys(BAR_SYMBOLS + held):
                        if history.bars_stale(sym, now):
                            try:
                                history.write_bars(sym, await b.daily_bars(sym))
                            except Exception as exc:  # noqa: BLE001
                                log.warning("daily bars for %s failed: %s", sym, exc)
                            await asyncio.sleep(1)
                if last_store is None or time.monotonic() - last_store > 300:
                    last_store = time.monotonic()
                    await ops.refresh_store_health()
                flex_due = last_flex is None or time.monotonic() - last_flex > 6 * 3600
                if flex_due and (flex_task is None or flex_task.done()):
                    last_flex = time.monotonic()
                    flex_task = asyncio.create_task(_flex_refresh(ctx.profile), name="flex-refresh")
                # once per start, connected or not: with the gateway down it fails fast on the
                # socket check, which says exactly what to fix
                if not doctor_started and (b.connected or b.status.error != "not connected yet"):
                    doctor_started = True
                    last = ops.last_doctor(ctx.profile)
                    if last is None or datetime.fromisoformat(last["ran_at"]) < now - timedelta(hours=12):
                        asyncio.create_task(ops.run_doctor(ctx.profile))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("maintenance step failed")
            await asyncio.sleep(MAINTENANCE_INTERVAL)
    finally:
        if flex_task is not None and not flex_task.done():
            flex_task.cancel()
