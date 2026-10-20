"""
doctor.py — end-to-end check that this machine can trade through IBKR.

    python -m src.broker.ibkr.doctor                  # paper, settings from env / broker.yaml
    python -m src.broker.ibkr.doctor --port 7497      # TWS paper
    python -m src.broker.ibkr.doctor --json           # machine-readable

Walks the same path the daily runner takes: socket → API handshake →
account → positions → contracts → historical bars → quotes → order
preview. Every failing step says what to change.

Nothing is traded. The order checks use IBKR's "whatIf" preview, which
returns margin and commission for a 1-share order without transmitting
it; they run by default for paper only. Exit code is 1 if any check FAILs.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import settings
from .client import is_paper_account, sync_failure

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

PORT_LABELS = {
    4002: "IB Gateway paper",
    4001: "IB Gateway live",
    7497: "TWS paper",
    7496: "TWS live",
}

MARKET_DATA_TYPES = {
    1: "live",
    2: "frozen (market closed, live subscription)",
    3: "delayed 15 min",
    4: "delayed-frozen",
}

# IBKR messages that accompany an accepted order rather than reject it: 10349
# ("Order TIF was set to DAY based on order preset") and the 21xx warnings.
# ib_async ends a whatIf request on the first of them, so the preview's margin
# figures don't come back, but the order was not refused.
INFORMATIONAL_CODES = {10349} | set(range(2100, 2200))

_UNIVERSE_PATH = Path(__file__).resolve().parents[3] / "config" / "universes" / "etfs.yaml"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    fix: str = ""


@dataclass
class _Ctx:
    ib: Any
    endpoint: settings.IBKREndpoint
    client_id: int
    timeout: float
    symbols: list[str]
    errors: list[tuple[int, int, str]] = field(default_factory=list)
    account: str | None = None
    reference: Any = None  # qualified contract used for bars / quotes / previews

    def on_error(self, req_id: int, code: int, message: str, *_: Any) -> None:
        self.errors.append((req_id, code, message))

    def errors_since(self, mark: int) -> list[tuple[int, int, str]]:
        return self.errors[mark:]


class _Stop(Exception):
    """Raised by a check whose failure makes the remaining checks meaningless."""

    def __init__(self, result: CheckResult) -> None:
        super().__init__(result.detail)
        self.result = result


def _fmt_money(value: float) -> str:
    return f"${value:,.0f}"


def _positive(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def _quote(ticker: Any) -> float | None:
    for attr in ("last", "close", "bid", "ask"):
        value = getattr(ticker, attr, None)
        if _positive(value):
            return float(value)
    return None


def default_symbols() -> list[str]:
    try:
        import yaml

        with _UNIVERSE_PATH.open("r", encoding="utf-8") as f:
            core = ((yaml.safe_load(f) or {}).get("etfs") or {}).get("core") or []
        symbols = [str(s).strip().upper() for s in core if str(s).strip()]
    except (OSError, ImportError):
        symbols = []
    return symbols or ["SPY"]


# ── checks ───────────────────────────────────────────────────────────────


def check_config(endpoint: settings.IBKREndpoint) -> CheckResult:
    span = max(settings.ROLE_OFFSETS.values())
    return CheckResult(
        "config",
        PASS,
        f"{endpoint.host}:{endpoint.port} (port from {endpoint.source['port']}), "
        f"client IDs {endpoint.client_id_base}-{endpoint.client_id_base + span}, "
        f"account: {endpoint.account or 'first managed account'}",
    )


def check_socket(endpoint: settings.IBKREndpoint) -> CheckResult:
    open_ports = [p for p in PORT_LABELS if settings.port_is_open(endpoint.host, p)]
    if endpoint.port not in PORT_LABELS and settings.port_is_open(endpoint.host, endpoint.port):
        open_ports.insert(0, endpoint.port)
    others = ", ".join(
        f"{p} ({PORT_LABELS.get(p, 'custom')})" for p in open_ports if p != endpoint.port
    )
    if endpoint.port in open_ports:
        label = PORT_LABELS.get(endpoint.port, "custom port")
        detail = f"{endpoint.port} listening ({label})"
        return CheckResult("socket", PASS, detail + (f"; also open: {others}" if others else ""))

    same_profile = [p for p in open_ports if p in settings.candidate_ports(endpoint.profile)]
    if same_profile:
        p = same_profile[0]
        fix = (
            f"{PORT_LABELS[p]} is listening on {p}. Set IBKR_PORT_{endpoint.profile.upper()}={p} "
            f"in .env (or ibkr.{endpoint.profile}.port in config/broker.yaml)."
        )
    else:
        fix = (
            "Start IB Gateway or TWS and log in to the "
            f"{endpoint.profile} account; enable API → 'Enable ActiveX and Socket Clients'. "
            "Docker (from the repo root): `docker compose -f ibgw/docker-compose.yml up -d`."
        )
    detail = f"nothing listening on {endpoint.host}:{endpoint.port}"
    if others:
        detail += f"; open: {others}"
    raise _Stop(CheckResult("socket", FAIL, detail, fix))


def check_handshake(ctx: _Ctx) -> CheckResult:
    ep = ctx.endpoint
    try:
        # raiseSyncErrors: a gateway cut off from IBKR answers the socket but not
        # the startup requests; without it the checks below would read "no
        # positions" and pass (src/broker/ibkr/client.py, IBKRSyncError)
        ctx.ib.connect(ep.host, ep.port, clientId=ctx.client_id, timeout=ctx.timeout, raiseSyncErrors=True)
    except Exception as exc:  # noqa: BLE001
        codes = {code for _, code, _ in ctx.errors}
        failed = sync_failure(exc)
        if failed is not None:
            raise _Stop(CheckResult(
                "handshake",
                FAIL,
                "IB Gateway answered, but IBKR's servers didn't: " + "; ".join(failed)
                + (" (warning 2110: the gateway lost its connection to IBKR)" if 2110 in codes else ""),
                "The gateway is cut off from IBKR; nothing it reports can be trusted until it "
                "reconnects, which it usually does by itself within minutes. Run the doctor again; "
                "if it still fails, restart the gateway (bin/tradeit gateway stop, then start).",
            )) from None
        if 326 in codes:
            fix = (
                f"clientId {ctx.client_id} is already connected. Close the other session "
                "or move the block with IBKR_CLIENT_ID_BASE_PAPER / _LIVE."
            )
        else:
            fix = (
                "The socket accepts connections but the API did not answer. In Gateway/TWS: "
                "Configure → Settings → API → Settings: tick 'Enable ActiveX and Socket "
                "Clients', add 127.0.0.1 to Trusted IPs, and dismiss any pending "
                "'accept incoming connection' dialog."
            )
        raise _Stop(CheckResult("handshake", FAIL, f"{type(exc).__name__}: {exc}", fix)) from None
    if not ctx.ib.isConnected():
        raise _Stop(CheckResult("handshake", FAIL, "connect() returned but session is down"))
    version = ctx.ib.client.serverVersion()
    return CheckResult("handshake", PASS, f"API server version {version}, clientId {ctx.client_id}")


def check_accounts(ctx: _Ctx) -> CheckResult:
    accounts = [str(a) for a in ctx.ib.managedAccounts()]
    if not accounts:
        raise _Stop(CheckResult("account", FAIL, "no managed accounts returned"))
    profile = ctx.endpoint.profile
    wrong = [a for a in accounts if is_paper_account(a) != (profile == "paper")]
    if wrong:
        raise _Stop(CheckResult(
            "account",
            FAIL,
            f"profile={profile} but this session serves {', '.join(wrong)}",
            "Wrong port: paper is 4002 (Gateway) / 7497 (TWS); live is 4001 / 7496.",
        ))
    configured = ctx.endpoint.account
    if configured and configured not in accounts:
        raise _Stop(CheckResult(
            "account",
            FAIL,
            f"configured account {configured} not in {', '.join(accounts)}",
            "Fix IBKR_ACCOUNT_* / ibkr.<profile>.account, or leave it empty.",
        ))
    ctx.account = configured or accounts[0]
    extra = f" (session has {len(accounts)} accounts)" if len(accounts) > 1 else ""
    return CheckResult("account", PASS, f"{ctx.account} ({profile}){extra}")


def check_account_summary(ctx: _Ctx) -> CheckResult:
    rows = {row.tag: row for row in ctx.ib.accountSummary(ctx.account)}
    if "NetLiquidation" not in rows:
        return CheckResult("balances", FAIL, "NetLiquidation missing from account summary")

    def num(tag: str) -> float | None:
        row = rows.get(tag)
        try:
            return float(row.value) if row is not None else None
        except (TypeError, ValueError):
            return None

    nlv = num("NetLiquidation")
    parts = [f"NLV {_fmt_money(nlv)} {rows['NetLiquidation'].currency}"]
    for tag, label in (
        ("TotalCashValue", "cash"),
        ("GrossPositionValue", "gross positions"),
        ("BuyingPower", "buying power"),
    ):
        value = num(tag)
        if value is not None:
            parts.append(f"{label} {_fmt_money(value)}")
    status = PASS if nlv and nlv > 0 else WARN
    return CheckResult("balances", status, ", ".join(parts))


def check_positions(ctx: _Ctx) -> CheckResult:
    positions = [p for p in ctx.ib.positions() if getattr(p, "account", ctx.account) == ctx.account]
    if not positions:
        return CheckResult("positions", PASS, "no open positions")
    shorts = sum(1 for p in positions if float(p.position) < 0)
    shown = ", ".join(f"{p.contract.symbol} {float(p.position):g}" for p in positions[:8])
    more = f", +{len(positions) - 8} more" if len(positions) > 8 else ""
    return CheckResult(
        "positions", PASS, f"{len(positions)} positions ({shorts} short): {shown}{more}"
    )


def check_orders_and_fills(ctx: _Ctx) -> CheckResult:
    trades = ctx.ib.reqAllOpenOrders()
    fills = ctx.ib.fills()
    orders_id = ctx.endpoint.client_id("orders")
    foreign = [t for t in trades if getattr(t.order, "clientId", orders_id) != orders_id]
    detail = (
        f"{len(trades)} open orders, {len(fills)} executions today "
        "(the API only returns today's fills; history needs a Flex query)"
    )
    if foreign:
        ids = sorted({t.order.clientId for t in foreign})
        return CheckResult(
            "orders",
            WARN,
            detail + f"; {len(foreign)} open orders belong to clientId(s) {ids}",
            f"Only the placing clientId can modify/cancel an order. New orders use clientId "
            f"{orders_id}; set it as 'Master API client ID' in Gateway/TWS API settings "
            "to see every order's status.",
        )
    return CheckResult("orders", PASS, detail)


def check_clock(ctx: _Ctx) -> CheckResult:
    server = ctx.ib.reqCurrentTime()
    if server.tzinfo is None:
        server = server.replace(tzinfo=timezone.utc)
    skew = abs((datetime.now(timezone.utc) - server).total_seconds())
    detail = f"server {server:%Y-%m-%d %H:%M:%S} UTC, local clock off by {skew:.0f}s"
    if skew > 60:
        return CheckResult("clock", FAIL, detail, "Enable automatic time sync on this machine.")
    if skew > 3:
        return CheckResult("clock", WARN, detail, "Enable automatic time sync; MOC cutoffs are exact.")
    return CheckResult("clock", PASS, detail)


def check_contracts(ctx: _Ctx) -> CheckResult:
    from ib_async import Stock

    contracts = [Stock(s, "SMART", "USD") for s in ctx.symbols]
    qualified = ctx.ib.qualifyContracts(*contracts)
    by_symbol = {
        s: c for s, c in zip(ctx.symbols, qualified)
        if c is not None and getattr(c, "conId", 0)
    }
    failed = [s for s in ctx.symbols if s not in by_symbol]
    ctx.reference = by_symbol.get("SPY") or next(iter(by_symbol.values()), None)
    if ctx.reference is None:
        raise _Stop(CheckResult(
            "contracts", FAIL, f"none of {len(ctx.symbols)} symbols qualified",
            "Check the symbols, and that the account has US stock/ETF trading permission.",
        ))
    if failed:
        return CheckResult(
            "contracts", FAIL,
            f"{len(by_symbol)}/{len(ctx.symbols)} qualified; failed: {', '.join(failed)}",
            "Unknown or ambiguous as Stock(symbol, 'SMART', 'USD'); production orders for "
            "these would fail. Fix the symbol or add a primary exchange.",
        )
    return CheckResult("contracts", PASS, f"all {len(ctx.symbols)} symbols qualified")


def check_historical(ctx: _Ctx) -> CheckResult:
    contract = ctx.reference
    mark = len(ctx.errors)
    bars = ctx.ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="5 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    if not bars:
        errs = "; ".join(f"{c}: {m}" for _, c, m in ctx.errors_since(mark)) or "no bars"
        return CheckResult(
            "history", FAIL, f"{contract.symbol} daily bars: {errs}",
            "Daily appends use this exact request. Error 162 = no permission/pacing; "
            "wait 10 minutes if you hit pacing limits.",
        )
    last = bars[-1].date
    last_day = last.date() if isinstance(last, datetime) else last
    age = (date.today() - last_day).days if isinstance(last_day, date) else None
    detail = f"{contract.symbol} {len(bars)} daily bars, last {last_day} close {bars[-1].close}"
    if age is not None and age > 5:
        return CheckResult("history", WARN, detail + f" ({age} days old)")
    return CheckResult("history", PASS, detail)


def check_market_data(ctx: _Ctx, sleep: Callable[[float], None] | None = None) -> CheckResult:
    contract = ctx.reference
    sleep = sleep or ctx.ib.sleep
    mark = len(ctx.errors)
    # Type 3 = "delayed if not subscribed": IBKR still sends live data when
    # the account has a subscription, so the ticker reports which one it got.
    ctx.ib.reqMarketDataType(3)
    ticker = ctx.ib.reqMktData(contract, "", False, False)
    deadline = time.monotonic() + ctx.timeout
    while _quote(ticker) is None and time.monotonic() < deadline:
        sleep(0.25)
    price = _quote(ticker)
    ctx.ib.cancelMktData(contract)

    if price is None:
        errs = "; ".join(f"{c}: {m}" for _, c, m in ctx.errors_since(mark)) or "no ticks"
        return CheckResult("quotes", WARN, f"{contract.symbol}: no quote in {ctx.timeout:.0f}s ({errs})")
    kind = int(getattr(ticker, "marketDataType", 1) or 1)
    detail = f"{contract.symbol} {price:.2f}, {MARKET_DATA_TYPES.get(kind, kind)}"
    if kind in (3, 4):
        return CheckResult(
            "quotes", WARN, detail,
            "Paper accounts only get real-time quotes shared from the live login: Client "
            "Portal (live user) → Settings → Paper Trading Account → share market data, "
            "and hold the US securities snapshot/streaming subscription.",
        )
    return CheckResult("quotes", PASS, detail)


def check_order_preview(ctx: _Ctx) -> list[CheckResult]:
    from ib_async import MarketOrder

    contract = ctx.reference
    held = sum(
        float(p.position) for p in ctx.ib.positions()
        if getattr(p, "account", ctx.account) == ctx.account
        and getattr(p.contract, "conId", None) == getattr(contract, "conId", None)
    )
    results = []
    for action, name in (("BUY", "order preview"), ("SELL", "short preview")):
        if action == "SELL" and held >= 1:
            results.append(CheckResult(name, SKIP, f"account is long {contract.symbol}; a 1-share sell is not a short"))
            continue
        mark = len(ctx.errors)
        raised = ""
        try:
            # tif set as the engine sets it on every order, so the preview isn't
            # answered with the "set to DAY based on order preset" note
            state = ctx.ib.whatIfOrder(contract, MarketOrder(action, 1, tif="DAY"))
        except Exception as exc:  # noqa: BLE001 — a timeout here is diagnostic
            state, raised = None, type(exc).__name__
        errs = ctx.errors_since(mark)
        if errs and all(c in INFORMATIONAL_CODES for _, c, _ in errs) and not (
            state and _positive_or_zero(getattr(state, "initMarginChange", None))
        ):
            notes = "; ".join(f"{c}: {m}" for _, c, m in errs)
            results.append(CheckResult(name, PASS, f"{action} 1 {contract.symbol}: accepted (IBKR notes: {notes})"))
            continue
        if state and _positive_or_zero(getattr(state, "initMarginChange", None)):
            commission = getattr(state, "commission", None)
            detail = (
                f"{action} 1 {contract.symbol}: accepted, margin change "
                f"{float(state.initMarginChange):,.2f}"
            )
            if _positive(commission):
                detail += f", commission {float(commission):.2f}"
            warning = getattr(state, "warningText", "")
            results.append(CheckResult(name, PASS, detail + (f" (note: {warning})" if warning else "")))
            continue

        text = "; ".join(f"{c}: {m}" for _, c, m in errs) or raised or "no response"
        if any(c == 321 and "read-only" in m.lower() for _, c, m in errs):
            fix = "API is read-only: Gateway/TWS → Configure → Settings → API → untick 'Read-Only API'."
        elif action == "SELL":
            fix = "Short sales need a margin account with short permission; long/short sleeves will be rejected."
        else:
            fix = "Orders would be rejected. Check trading permissions for US ETFs and the API settings."
        results.append(CheckResult(name, FAIL, f"{action} 1 {contract.symbol}: {text}", fix))
    return results


def _positive_or_zero(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and abs(v) < 1e300  # UNSET_DOUBLE is ~1.8e308


# ── runner ───────────────────────────────────────────────────────────────


def run_doctor(
    profile: str = "paper",
    *,
    host: str | None = None,
    port: int | None = None,
    symbols: list[str] | None = None,
    timeout: float = 10.0,
    order_preview: bool | None = None,
    ib_factory: Callable[[], Any] | None = None,
) -> list[CheckResult]:
    endpoint = settings.resolve_endpoint(profile, host=host, port=port)
    if order_preview is None:
        order_preview = endpoint.profile == "paper"
    results = [check_config(endpoint)]
    planned = ["socket", "handshake", "account", "balances", "positions", "orders",
               "clock", "contracts", "history", "quotes"]
    if order_preview:
        planned += ["order preview", "short preview"]

    def skip_rest(reason: str) -> None:
        done = {r.name for r in results}
        results.extend(CheckResult(n, SKIP, reason) for n in planned if n not in done)

    try:
        results.append(check_socket(endpoint))
    except _Stop as stop:
        results.append(stop.result)
        skip_rest("no socket")
        return results

    if ib_factory is None:
        from ib_async import IB as ib_factory  # noqa: N813
    ib = ib_factory()
    ib.RequestTimeout = timeout  # ib_async waits forever on some failures (e.g. read-only API)
    ctx = _Ctx(
        ib=ib,
        endpoint=endpoint,
        client_id=endpoint.client_id("doctor"),
        timeout=timeout,
        symbols=[s.strip().upper() for s in (symbols or default_symbols()) if s.strip()],
    )
    ib.errorEvent += ctx.on_error

    steps: list[Callable[[_Ctx], Any]] = [
        check_handshake, check_accounts, check_account_summary, check_positions,
        check_orders_and_fills, check_clock, check_contracts, check_historical,
        check_market_data,
    ]
    if order_preview:
        steps.append(check_order_preview)
    try:
        for step in steps:
            try:
                out = step(ctx)
            except _Stop as stop:
                results.append(stop.result)
                skip_rest(f"stopped after {stop.result.name}")
                break
            except Exception as exc:  # noqa: BLE001 — one broken check shouldn't hide the rest
                name = step.__name__.removeprefix("check_").replace("_", " ")
                results.append(CheckResult(name, FAIL, f"{type(exc).__name__}: {exc}"))
                continue
            results.extend(out if isinstance(out, list) else [out])
    finally:
        if ib.isConnected():
            ib.disconnect()
    return results


def render(results: list[CheckResult], endpoint: settings.IBKREndpoint) -> str:
    lines = [f"IBKR doctor · profile {endpoint.profile} · {endpoint.host}:{endpoint.port}", ""]
    width = max(len(r.name) for r in results)
    for r in results:
        lines.append(f"  {r.status:<4}  {r.name:<{width}}  {r.detail}")
        if r.fix and r.status in (WARN, FAIL):
            lines.append(f"        {'':<{width}}  → {r.fix}")
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    lines += ["", f"{fails} failed, {warns} warnings" if fails or warns else "All checks passed."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", choices=settings.PROFILES, default="paper")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--symbols", help="comma-separated; default: config/universes/etfs.yaml core")
    parser.add_argument("--timeout", type=float, default=10.0)
    preview = parser.add_mutually_exclusive_group()
    preview.add_argument("--order-preview", dest="order_preview", action="store_true", default=None,
                         help="run whatIf order previews (default: paper only)")
    preview.add_argument("--no-order-preview", dest="order_preview", action="store_false")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    symbols = args.symbols.split(",") if args.symbols else None
    results = run_doctor(
        args.profile,
        host=args.host,
        port=args.port,
        symbols=symbols,
        timeout=args.timeout,
        order_preview=args.order_preview,
    )
    endpoint = settings.resolve_endpoint(args.profile, host=args.host, port=args.port)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(render(results, endpoint))
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
