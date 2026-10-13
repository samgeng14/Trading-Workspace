"""One long-lived IBKR connection for the dashboard.

Connects on the ``dashboard`` client ID from trading-engine's settings
(paper: 307, live: 407), so it never collides with the daily runner's order
connection. Reconnects on its own when IB Gateway restarts. Refuses a live
account under the paper profile, and the reverse.

Everything the API reads from IBKR goes through ``snapshot()``, ``orders()``
and ``quotes()``, which return plain data; the routes never touch ib_async.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import engine, nyse
from .universe import STRIP, lookup

log = logging.getLogger(__name__)

# 1 live, 2 frozen, 3 delayed, 4 delayed-frozen. Asking for 4 gets live data
# where the account has the subscription and the last delayed value otherwise.
MARKET_DATA_TYPE = 4
RECONNECT_S = (2, 5, 10, 30)
INTRADAY_EVERY_S = 15  # intraday P&L sample; the API adds the live value as the chart's last point


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) or abs(v) > 1e300 else v


@dataclass
class PositionSnap:
    con_id: int
    symbol: str  # display symbol: localSymbol for futures (MESZ6)
    root: str  # contract.symbol (MES)
    sec_type: str  # STK / ETF / FUT / OPT
    quantity: float
    multiplier: float
    avg_cost: float  # per unit, not per contract
    last: float | None
    prev_close: float | None
    market_value: float  # signed; notional for futures
    unrealized_pnl: float
    day_pnl: float | None


@dataclass
class AccountSnap:
    account: str
    currency: str
    nav: float
    cash: float
    day_pnl: float | None
    as_of: datetime
    positions: list[PositionSnap]
    source: str = "ibkr"  # or "snapshot file"


@dataclass
class BrokerStatus:
    connected: bool
    endpoint: str
    account: str | None = None
    error: str | None = None
    since: datetime | None = None
    market_data_type: int | None = None


@dataclass
class _Line:
    """A market-data subscription: the ticker and what it is for."""

    ticker: Any
    symbol: str
    label: str = ""
    kind: str = ""


@dataclass
class Broker:
    profile: str = "paper"
    ib_factory: Callable[[], Any] | None = None
    host: str | None = None
    port: int | None = None

    status: BrokerStatus = field(init=False)
    _ib: Any = field(init=False, default=None)
    _task: asyncio.Task | None = field(init=False, default=None)
    _pnl: Any = field(init=False, default=None)
    _pnl_single: dict[int, Any] = field(init=False, default_factory=dict)
    _position_lines: dict[int, _Line] = field(init=False, default_factory=dict)
    _strip_lines: list[_Line] = field(init=False, default_factory=list)
    _intraday: list[tuple[datetime, float]] = field(init=False, default_factory=list)
    _intraday_date: date | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        from src.broker.ibkr import settings  # trading-engine

        self.endpoint = settings.resolve_endpoint(self.profile, host=self.host, port=self.port)
        self.client_id = self.endpoint.client_id("dashboard")
        self.status = BrokerStatus(
            connected=False,
            endpoint=f"{self.endpoint.host}:{self.endpoint.port}",
            error="not connected yet",
        )

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ibkr-connection")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    async def _run(self) -> None:
        attempt = 0
        while True:
            if self._ib is None or not self._ib.isConnected():
                if self.status.connected:
                    self.status.connected = False
                    self.status.error = self.status.error or "connection lost"
                try:
                    await self._connect()
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    error = _describe(exc, self.endpoint)
                    if error != self.status.error:
                        log.warning("IBKR: %s", error)
                    self.status = BrokerStatus(connected=False, endpoint=self.status.endpoint, error=error)
                    await asyncio.sleep(RECONNECT_S[min(attempt, len(RECONNECT_S) - 1)])
                    attempt += 1
                    continue
            self._sample_intraday()
            await asyncio.sleep(INTRADAY_EVERY_S if self.status.connected else 2)

    async def _connect(self) -> None:
        factory = self.ib_factory
        if factory is None:
            from ib_async import IB as factory  # noqa: N813
        ib = factory()
        ib.RequestTimeout = 20
        await ib.connectAsync(
            self.endpoint.host,
            self.endpoint.port,
            clientId=self.client_id,
            timeout=10,
            account=self.endpoint.account or "",
        )
        try:
            await self._set_up(ib)
        except BaseException:
            # never leave a half-set-up session holding the client ID; the loop retries
            ib.disconnect()
            raise
        log.info("connected to IBKR %s as client %s, account %s", self.status.endpoint, self.client_id, self.account)

    async def _set_up(self, ib: Any) -> None:
        from src.broker.ibkr.client import is_paper_account  # trading-engine

        accounts = list(ib.managedAccounts())
        account = self.endpoint.account or (accounts[0] if accounts else "")
        if not account:
            raise ConnectionError("IBKR reported no managed accounts")
        paper = is_paper_account(account)
        if (self.endpoint.profile == "paper") != paper:
            raise PermissionError(
                f"account {account} is a {'paper' if paper else 'live'} account but the API runs the "
                f"{self.endpoint.profile} profile; refusing to show or trade it"
            )
        if not self.endpoint.account and len(accounts) > 1:
            await ib.reqAccountUpdatesAsync(account)

        self._ib = ib
        self.account = account
        ib.reqMarketDataType(MARKET_DATA_TYPE)
        self._pnl = ib.reqPnL(account)
        self._pnl_single.clear()
        self._position_lines.clear()
        await self._subscribe_strip()
        await self._sync_positions()
        ib.positionEvent += lambda *_: self._sync_position_subscriptions()
        ib.disconnectedEvent += self._on_disconnected
        self._load_intraday()
        self.status = BrokerStatus(
            connected=True,
            endpoint=self.status.endpoint,
            account=account,
            since=datetime.now(timezone.utc),
        )

    def _on_disconnected(self) -> None:
        self.status.connected = False
        self.status.error = "IB Gateway closed the connection (restart or network); reconnecting"

    async def _subscribe_strip(self) -> None:
        from ib_async import Contract

        contracts = []
        for symbol, _label, _kind, spec in STRIP:
            c = Contract(
                symbol=symbol,
                currency=spec.get("currency", "USD"),
                **{k: v for k, v in spec.items() if k != "currency"},
            )
            contracts.append(c)
        try:
            qualified = await self._ib.qualifyContractsAsync(*contracts)
        except Exception:  # noqa: BLE001 — the strip is optional
            qualified = []
        by_symbol = {c.symbol: c for c in qualified if c is not None and getattr(c, "conId", 0)}
        self._strip_lines = []
        for symbol, label, kind, _spec in STRIP:
            c = by_symbol.get(symbol)
            if c is not None:
                self._strip_lines.append(_Line(self._ib.reqMktData(c), symbol, label, kind))

    def _sync_position_subscriptions(self) -> None:
        if self._ib is not None:
            asyncio.ensure_future(self._sync_positions())

    async def _sync_positions(self) -> None:
        """Keep one P&L line and one quote line per held contract."""
        from ib_async import Contract

        ib = self._ib
        held = {p.contract.conId for p in ib.positions(self.account) if p.position}
        for con_id in list(self._pnl_single):
            if con_id not in held:
                ib.cancelPnLSingle(self.account, "", con_id)
                del self._pnl_single[con_id]
                line = self._position_lines.pop(con_id, None)
                if line is not None:
                    ib.cancelMktData(line.ticker.contract)
        new = [con_id for con_id in held if con_id not in self._pnl_single]
        for con_id in new:
            self._pnl_single[con_id] = ib.reqPnLSingle(self.account, "", con_id)
        if not new:
            return
        try:  # position reports carry no exchange; qualifying by conId fills it in
            contracts = await ib.qualifyContractsAsync(*[Contract(conId=c) for c in new])
        except Exception as exc:  # noqa: BLE001
            log.warning("could not qualify held contracts: %s", exc)
            return
        for c in contracts:
            if c is None or not getattr(c, "conId", 0):
                continue
            if c.secType == "STK":
                c.exchange = "SMART"
            self._position_lines[c.conId] = _Line(ib.reqMktData(c), c.symbol)

    # ── reads ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self.status.connected and self._ib is not None and self._ib.isConnected()

    def market_data_type(self) -> int | None:
        """The worst feed among quotes that answered: one delayed line means the book is priced
        on delayed data somewhere (FX quotes are free real-time, so the best would mislead)."""
        types = [
            line.ticker.marketDataType
            for line in [*self._position_lines.values(), *self._strip_lines]
            if _num(line.ticker.last) is not None or _num(line.ticker.close) is not None
        ]
        return max(types) if types else None

    def snapshot(self) -> AccountSnap | None:
        if not self.connected:
            return None
        ib = self._ib
        values: dict[str, float] = {}
        currency = "USD"
        for v in ib.accountValues(self.account):
            if v.currency in ("BASE", "USD") and v.tag in ("NetLiquidation", "TotalCashValue"):
                n = _num(v.value)
                if n is not None and (v.tag not in values or v.currency == "BASE"):
                    values[v.tag] = n
            if v.tag == "Currency" and v.currency == "BASE":
                currency = v.value or currency
        nav = values.get("NetLiquidation")
        if nav is None:
            return None

        positions = []
        for item in ib.portfolio(self.account):
            if not item.position:
                continue
            c = item.contract
            sec_type = "ETF" if c.secType == "STK" and c.symbol in _etf_symbols() else c.secType
            mult = _num(c.multiplier) or 1.0
            line = self._position_lines.get(c.conId)
            ticker = line.ticker if line else None
            last = _num(ticker.last) if ticker else None
            if last is None and ticker is not None:
                last = _num(ticker.marketPrice())
            last = last if last is not None else _num(item.marketPrice)
            prev_close = _num(ticker.close) if ticker else None
            pnl = self._pnl_single.get(c.conId)
            single_value = _num(pnl.value) if pnl is not None else None
            if last is not None:
                market_value = item.position * last * mult
            else:
                market_value = single_value if single_value is not None else (_num(item.marketValue) or 0.0)
            positions.append(
                PositionSnap(
                    con_id=c.conId,
                    symbol=c.localSymbol if c.secType == "FUT" and c.localSymbol else c.symbol,
                    root=c.symbol,
                    sec_type=sec_type if sec_type in ("STK", "ETF", "FUT", "OPT") else "STK",
                    quantity=float(item.position),
                    multiplier=mult,
                    avg_cost=(_num(item.averageCost) or 0.0) / (mult if c.secType in ("FUT", "OPT") else 1.0),
                    last=last,
                    prev_close=prev_close,
                    market_value=market_value,
                    unrealized_pnl=_num(item.unrealizedPNL) or 0.0,
                    day_pnl=_num(pnl.dailyPnL) if pnl is not None else None,
                )
            )
        return AccountSnap(
            account=self.account,
            currency=currency,
            nav=nav,
            cash=values.get("TotalCashValue", 0.0),
            day_pnl=_num(self._pnl.dailyPnL) if self._pnl is not None else None,
            as_of=datetime.now(timezone.utc),
            positions=positions,
        )

    def quotes(self) -> list[dict[str, Any]]:
        out = []
        for line in self._strip_lines:
            t = line.ticker
            last = _num(t.last)
            if last is None:
                last = _num(t.marketPrice())
            prev = _num(t.close)
            if last is None or prev is None or prev == 0:
                continue
            if line.kind == "yield":  # CBOE TNX quotes 10x the yield
                last, prev = last / 10, prev / 10
            out.append(
                {
                    "symbol": line.symbol,
                    "label": line.label,
                    "last": last,
                    "change": last - prev,
                    "change_pct": last / prev - 1,
                    "kind": line.kind,
                    "_type": t.marketDataType,
                }
            )
        return out

    def trades(self) -> list[Any]:
        """Every order IBKR knows about this session, from any client."""
        if not self.connected:
            return []
        seen, out = set(), []
        for t in self._ib.trades():
            key = t.order.permId or (t.order.clientId, t.order.orderId)
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out

    async def refresh_orders(self) -> None:
        """Pick up orders placed by other clients (the daily runner) since we connected."""
        if self.connected:
            try:
                await asyncio.wait_for(self._ib.reqAllOpenOrdersAsync(), 10)
                await asyncio.wait_for(self._ib.reqCompletedOrdersAsync(False), 10)
            except Exception as exc:  # noqa: BLE001
                log.warning("order refresh failed: %s", exc)

    async def daily_bars(self, symbol: str, duration: str = "2 Y") -> list[tuple[date, float]]:
        """Dividend-adjusted daily closes for a US ETF/stock, oldest first."""
        if not self.connected:
            raise ConnectionError(self.status.error or "IBKR not connected")
        from ib_async import Stock

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        bars = await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow="ADJUSTED_LAST",
            useRTH=True,
            formatDate=1,
        )
        return [(b.date.date() if isinstance(b.date, datetime) else b.date, float(b.close)) for b in bars or []]

    # ── orders ───────────────────────────────────────────────────────────

    async def cancel(self, trade: Any) -> None:
        """Cancel an open order. IBKR only lets the client that placed an order cancel
        it, so orders from the daily runner are cancelled over a brief connection
        on the runner's client ID."""
        o = trade.order
        if o.clientId == self.client_id:
            self._ib.cancelOrder(o)
            return
        from ib_async.ib import StartupFetch

        factory = self.ib_factory
        if factory is None:
            from ib_async import IB as factory  # noqa: N813
        tmp = factory()
        try:
            await tmp.connectAsync(
                self.endpoint.host,
                self.endpoint.port,
                clientId=o.clientId,
                timeout=10,
                fetchFields=StartupFetch.ORDERS_OPEN,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"could not connect as client {o.clientId}, which placed this order ({exc or type(exc).__name__}). "
                "If the daily runner is mid-run, wait for it to finish, or cancel in TWS"
            ) from exc
        try:
            mine = next((t for t in tmp.openTrades() if t.order.permId == o.permId), None)
            if mine is None:
                raise RuntimeError("the order is no longer open")
            tmp.cancelOrder(mine.order)
            await asyncio.sleep(1.0)
        finally:
            tmp.disconnect()

    # ── intraday P&L path ────────────────────────────────────────────────

    def _intraday_path(self, d: date) -> Path:
        folder = engine.api_state_dir() / "intraday"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{self.profile}_{d.isoformat()}.csv"

    def _load_intraday(self) -> None:
        state = nyse.session_state(datetime.now(timezone.utc))
        self._intraday_date = state.trading_date
        self._intraday = read_intraday(self._intraday_path(state.trading_date))

    def _sample_intraday(self) -> None:
        if not self.connected or self._pnl is None:
            return
        now = datetime.now(timezone.utc)
        state = nyse.session_state(now)
        if state.session != "open":
            return
        if self._intraday_date != state.trading_date:
            self._intraday_date, self._intraday = state.trading_date, []
        pnl = _num(self._pnl.dailyPnL)
        if pnl is None:
            return
        self._intraday.append((now, pnl))
        with self._intraday_path(state.trading_date).open("a", newline="") as f:
            csv.writer(f).writerow([now.isoformat(timespec="seconds"), f"{pnl:.2f}"])

    def intraday(self, trading_date: date) -> list[tuple[datetime, float]]:
        if self._intraday_date == trading_date and self._intraday:
            return list(self._intraday)
        return read_intraday(self._intraday_path(trading_date))


def read_intraday(path: Path) -> list[tuple[datetime, float]]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for row in csv.reader(f):
            try:
                out.append((datetime.fromisoformat(row[0]), float(row[1])))
            except (IndexError, ValueError):
                continue
    return out


def _etf_symbols() -> set[str]:
    from .universe import BY_SYMBOL

    return set(BY_SYMBOL)


def _describe(exc: BaseException, endpoint: Any) -> str:
    """Turn a connection failure into something a person can act on."""
    text = str(exc) or type(exc).__name__
    where = f"{endpoint.host}:{endpoint.port}"
    if isinstance(exc, ConnectionRefusedError) or "refused" in text.lower():
        return (
            f"nothing is listening on {where}. Start IB Gateway or TWS ({endpoint.profile}) and enable the API "
            "(Configure → Settings → API → Enable ActiveX and Socket Clients)"
        )
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return (
            f"{where} accepted the connection but the API handshake timed out. Check that 127.0.0.1 is a trusted "
            "IP in the API settings and that no other app is using client ID "
            f"{endpoint.client_id('dashboard')}"
        )
    if isinstance(exc, PermissionError):
        return text
    return f"{type(exc).__name__}: {text}"


def lookup_position(p: PositionSnap):
    return lookup(p.root, "FUT" if p.sec_type == "FUT" else "STK")
