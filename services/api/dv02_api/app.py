"""FastAPI app implementing schemas/api/v1.yaml over real data."""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from . import engine, flex, history, nyse, ops, orders, performance, rebalance, risk, runs, stats
from .broker import AccountSnap, Broker, PositionSnap, lookup_position
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
    positions = []
    for r in rows:
        px = history.closes(r["symbol"])
        last = px[-1][1] if px else r["avg_cost"]
        prev = px[-2][1] if len(px) > 1 else last
        positions.append(
            PositionSnap(
                con_id=0, symbol=r["symbol"], root=r["symbol"],
                sec_type="ETF" if r["symbol"] in BY_SYMBOL else "STK",
                quantity=r["quantity"], multiplier=1.0, avg_cost=r["avg_cost"], last=last, prev_close=prev,
                market_value=r["quantity"] * last, unrealized_pnl=r["quantity"] * (last - r["avg_cost"]), day_pnl=None,
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


def position_rows(ctx: Ctx, snap: AccountSnap) -> list[dict[str, Any]]:
    tw, _ = targets(ctx)
    etf_sleeve = sleeve(ctx)
    out = []
    for p in snap.positions:
        inst = lookup_position(p)
        unit = p.quantity * p.multiplier
        last = p.last if p.last is not None else (p.market_value / unit if unit else 0.0)
        prev = p.prev_close if p.prev_close else last
        day_pnl = p.day_pnl if p.day_pnl is not None else unit * (last - prev)
        cost = p.avg_cost * unit
        out.append(
            {
                "symbol": p.symbol,
                "name": inst.name if inst.name != inst.symbol else p.symbol,
                "asset_class": inst.asset_class,
                "sleeve": "Hedge" if p.sec_type == "FUT" else etf_sleeve,
                "sec_type": p.sec_type,
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


def nav_series(ctx: Ctx, snap: AccountSnap | None, now: datetime) -> tuple[list[tuple[date, float]], list[str]]:
    live = None
    if snap is not None and snap.source == "ibkr":
        live = (nyse.session_state(now).trading_date, snap.nav)
    return history.nav_history(ctx.profile, live)


# ── app ────────────────────────────────────────────────────────────────


def create_app(profile: str | None = None, broker: Any = None, background: bool = True) -> FastAPI:
    profile = (profile or os.environ.get("DV02_PROFILE", "paper")).lower()
    if profile not in ("paper", "live"):
        raise ValueError(f"profile must be paper or live, got {profile!r}")
    ctx = Ctx(profile=profile, broker=broker if broker is not None else Broker(profile))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks: list[asyncio.Task] = []
        if background:
            await ctx.broker.start()
            tasks = [asyncio.create_task(_maintenance(ctx), name="maintenance")]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            if background:
                await ctx.broker.stop()

    app = FastAPI(title="DV02 Fund API", version="1.0.0", lifespan=lifespan)
    app.state.ctx = ctx

    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem) -> JSONResponse:
        body = {"title": exc.title}
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status, media_type="application/problem+json")

    @app.exception_handler(FileNotFoundError)
    async def _missing(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(
            {"title": "Engine data not found", "detail": f"{exc}. Run the daily runner once to create it."},
            status_code=503,
            media_type="application/problem+json",
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
        navs, _ = nav_series(ctx, snap, now)
        start, _capital = history.inception()
        day_pnl = snap.day_pnl if snap.day_pnl is not None else sum(r["day_pnl"] for r in rows)
        last = navs[-1][0]
        month_base = date(last.year, last.month, 1) - timedelta(days=1)
        year_base = date(last.year - 1, 12, 31)
        spy = history.closes("SPY")
        spy_base = next((c for d, c in reversed(spy) if d <= start), spy[0][1] if spy else None)
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
            "mtd_return": performance.period_return(navs, month_base),
            "ytd_return": performance.period_return(navs, year_base),
            "itd_return": navs[-1][1] / navs[0][1] - 1,
            "benchmark": BENCHMARKS[0],
            "benchmark_itd_return": spy[-1][1] / spy_base - 1 if spy and spy_base else 0.0,
            "long_exposure": long_,
            "short_exposure": short,
            "gross_exposure": long_ - short,
            "net_exposure": long_ + short,
            "beta": sum(r["weight"] * r["beta"] for r in rows),
            "sharpe_itd": stats.sharpe(rets),
            "positions_count": len(rows),
            "nav_history": [{"date": d.isoformat(), "value": v} for d, v in navs[-30:]],
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
        if range_ not in ("1M", "3M", "6M", "YTD", "1Y", "ITD"):
            raise Problem(400, "Unknown range", range_)
        bench = next((b for b in BENCHMARKS if b["id"] == benchmark), None)
        if bench is None:
            raise Problem(400, "Unknown benchmark", benchmark)
        navs, sources = nav_series(ctx, ctx.broker.snapshot(), _now())
        levels = history.benchmark_levels(benchmark)
        if not levels:
            raise Problem(503, f"No price history for {benchmark}", "Start IB Gateway so the API can load daily bars.")
        try:
            return performance.performance(navs, levels, range_, bench)
        except ValueError:
            raise Problem(
                503,
                "Not enough NAV history yet",
                f"{len(navs)} day(s) from {', '.join(sources)}. Set up IBKR Flex (services/api/README.md) to load "
                "daily NAV since inception; the API also records each close from now on.",
            ) from None

    # ── risk ──────────────────────────────────────────────────────────
    @app.get(f"{v1}/risk")
    async def get_risk() -> dict[str, Any]:
        from src.portfolio.risk.exposure_limits import DEFAULT_EXPOSURE_LIMITS as engine_limits

        now = _now()
        snap = require_snapshot(ctx)
        rows = position_rows(ctx, snap)
        navs, _ = nav_series(ctx, snap, now)
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
            "gross": engine_limits.max_gross_exposure_pct,
            "net": engine_limits.max_net_exposure_pct,
            "single_name": engine_limits.max_single_position_pct,
        }
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
        )
        for lim in doc["limits"]:
            enforced = lim["id"] in ("gross", "net", "single_name")
            note = "the engine blocks orders above this" if enforced else "monitored here, not enforced by the engine"
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
        body = await request.json()
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
        body = await request.json()
        armed = bool(body.get("armed")) if isinstance(body, dict) else False
        reason = (body.get("reason") or "").strip() if isinstance(body, dict) else ""
        if armed and not reason:
            raise Problem(400, "A reason is required to arm the kill switch")
        return ops.set_kill_switch(armed, reason, by=f"dashboard ({getpass.getuser()})")

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

    return app


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

    installed = runs.launch_agent_installed()
    last = next((r for r in runs.load_runs(5) if not r["dry_run"]), None)
    last_text = f"last run {last['started_at'][:16].replace('T', ' ')}: {last['summary']}" if last else "no runs yet"
    if installed is False:
        sched = {
            "status": "warning",
            "detail": "LaunchAgent not installed: python -m src.production.scheduler --install-launchagent",
        }
    elif last is not None and not last["ok"]:
        sched = {"status": "warning", "detail": last_text}
    elif installed is None:
        sched = {"status": "unknown", "detail": f"launchd not available on this OS; {last_text}"}
    else:
        sched = {"status": "ok", "detail": f"Daily run {runs.runner_time():%H:%M} local; {last_text}"}

    flex_note = "IBKR Flex NAV loaded" if flex.read_cache(ctx.profile) else "no Flex NAV yet"
    return [
        {"id": "gateway", "label": "IB Gateway", **gateway},
        {"id": "market_data", "label": "Market data", **market},
        {"id": "engine", "label": "Engine", "status": "unknown",
         "detail": "C++ engine not deployed yet (Phase 3); the Python daily runner trades"},
        {"id": "scheduler", "label": "Scheduler", **sched},
        {"id": "database", "label": "Database", "status": "unknown",
         "detail": f"Postgres not configured yet (Phase 2); file store under trading-engine/data; {flex_note}"},
    ]  # fmt: skip


# ── background upkeep ──────────────────────────────────────────────────


async def _maintenance(ctx: Ctx) -> None:
    """Record closing NAV, keep daily bars and Flex NAV fresh, run the doctor once a day."""
    last_flex = last_bars = 0.0
    doctor_started = False
    while True:
        try:
            now = _now()
            b = ctx.broker
            s = nyse.session_state(now)
            snap = b.snapshot() if b.connected else None
            if snap is not None and s.session != "open" and nyse.et_date(now) == s.trading_date:
                history.record_close_nav(ctx.profile, s.trading_date, snap.nav)
            if b.connected and time.monotonic() - last_bars > 1800:
                last_bars = time.monotonic()
                held = [p.symbol for p in (snap.positions if snap else []) if p.sec_type in ("STK", "ETF")]
                for sym in dict.fromkeys(BAR_SYMBOLS + held):
                    if history.bars_stale(sym, now):
                        try:
                            history.write_bars(sym, await b.daily_bars(sym))
                        except Exception as exc:  # noqa: BLE001
                            log.warning("daily bars for %s failed: %s", sym, exc)
                        await asyncio.sleep(1)
            if time.monotonic() - last_flex > 6 * 3600:
                last_flex = time.monotonic()
                try:
                    log.info(await asyncio.to_thread(flex.refresh, ctx.profile))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Flex refresh failed: %s", exc)
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
        await asyncio.sleep(60)
