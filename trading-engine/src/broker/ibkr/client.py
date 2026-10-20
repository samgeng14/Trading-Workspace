from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, Optional, TypeVar

from ib_async import IB, Stock

logger = logging.getLogger(__name__)


class IBKRClientError(RuntimeError):
    """Base error for IBKR client failures."""


class IBKRNotConnectedError(IBKRClientError):
    """Raised when an operation requires an active IB connection."""


class IBKRWrongAccountError(IBKRClientError):
    """Raised when a paper-profile connection lands on a live account, or vice versa."""


class IBKRSyncError(IBKRClientError):
    """IB Gateway answered, but IBKR's servers didn't: the startup sync timed out.

    On 2026-10-19 the VM's gateway had lost its own link to IBKR (warning 2110)
    while its API socket still answered. ib_async logged "positions request
    timed out" and carried on with no positions; the end-of-day reconcile read
    that as "the account holds nothing" and armed the kill switch. Every
    connection now fails like this instead, so nothing acts on an empty read.
    """


# The longest any one request may take once connected (bars, contract details,
# the account summary). ib_async's default, 0, waits forever: on that same day
# the run's first bars request hung until systemd stopped it 45 minutes later.
REQUEST_TIMEOUT_S = 60.0


def sync_failure(exc: BaseException) -> list[str] | None:
    """The startup requests that timed out, when ``exc`` is the ``ConnectionError``
    ib_async raises from ``connect(raiseSyncErrors=True)``; None for anything else
    (a refused socket is a ``ConnectionError`` too, with an errno instead of a list)."""
    if isinstance(exc, ConnectionError) and exc.args and isinstance(exc.args[0], list):
        return [str(e) for e in exc.args[0]]
    return None


def sync_failure_message(where: str, failed: list[str]) -> str:
    return (
        f"IB Gateway answered at {where}, but IBKR's servers didn't ({'; '.join(failed)}). "
        "The gateway has lost its own connection to IBKR (warning 2110), so nothing it reports "
        "now can be trusted, positions least of all. It usually reconnects by itself within "
        "minutes; bin/tradeit doctor says when it has."
    )


def connect_ib(
    host: str,
    port: int,
    client_id: int,
    *,
    timeout: float = 5.0,
    request_timeout: float = REQUEST_TIMEOUT_S,
    ib: Any = None,
    **kwargs: Any,
) -> Any:
    """A connected ``ib_async.IB`` for code that drives it directly (the bar
    appends), with the same two guards as :class:`IBKRClient`: a startup sync that
    times out raises :class:`IBKRSyncError`, and no later request waits more than
    ``request_timeout`` seconds."""
    ib = ib if ib is not None else IB()
    ib.RequestTimeout = request_timeout
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, raiseSyncErrors=True, **kwargs)
    except ConnectionError as exc:
        failed = sync_failure(exc)
        if failed is not None:
            raise IBKRSyncError(sync_failure_message(f"{host}:{port}", failed)) from exc
        raise
    return ib


T = TypeVar("T")


def fail_fast(fetch: Callable[..., T], timeout_s: float = REQUEST_TIMEOUT_S) -> Callable[..., T]:
    """``fetch``, except that after one call times out every later call fails at
    once. The loops that call it per symbol catch each error and move on, so a
    gateway that stopped answering mid-run would otherwise cost ``timeout_s`` per
    symbol, over twenty minutes for the model's 21 ETFs."""
    stalled: list[str] = []

    def wrapped(*args: Any, **kwargs: Any) -> T:
        if stalled:
            raise TimeoutError(f"not asked: IBKR stopped answering ({stalled[0]})")
        try:
            return fetch(*args, **kwargs)
        except TimeoutError:
            stalled.append(f"a request timed out after {timeout_s:.0f}s")
            raise

    return wrapped


def is_paper_account(account_id: str) -> bool:
    """IBKR paper account IDs start with "D" (DU…, DF…); live ones never do."""
    return str(account_id).strip().upper().startswith("D")


@dataclass(slots=True)
class IBKRConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 1
    readonly: bool = False
    account: str | None = None
    timeout: float = 10.0
    # "paper" / "live": verify the session's accounts match on connect.
    # None skips the check (legacy call sites).
    expected_profile: str | None = None
    # seconds one request may take once connected; see REQUEST_TIMEOUT_S
    request_timeout: float = REQUEST_TIMEOUT_S


class IBKRClient:
    """Thin sync wrapper around ib_async.IB for paper/live execution.

    This keeps the rest of your codebase free from direct IB object handling
    until you are ready to push more broker logic deeper into the stack.
    """

    def __init__(self, config: IBKRConnectionConfig) -> None:
        self.config = config
        self.ib = IB()

    def connect(self) -> None:
        """Open a socket connection to TWS / IB Gateway."""
        if self.ib.isConnected():
            logger.info("IBKR already connected")
            return

        logger.info(
            "Connecting to IBKR host=%s port=%s client_id=%s readonly=%s",
            self.config.host,
            self.config.port,
            self.config.client_id,
            self.config.readonly,
        )
        self.ib.RequestTimeout = self.config.request_timeout
        try:
            self.ib.connect(
                self.config.host,
                self.config.port,
                clientId=self.config.client_id,
                readonly=self.config.readonly,
                timeout=self.config.timeout,
                # A startup request that times out means the gateway is up but cut
                # off from IBKR: fail here rather than carry on with no positions.
                raiseSyncErrors=True,
            )
        except ConnectionError as exc:
            failed = sync_failure(exc)
            if failed is not None:
                raise IBKRSyncError(
                    sync_failure_message(f"{self.config.host}:{self.config.port}", failed)
                ) from exc
            raise
        if not self.ib.isConnected():
            raise IBKRClientError("IBKR connection failed")
        self._verify_profile()

    def _verify_profile(self) -> None:
        expected = self.config.expected_profile
        if expected is None:
            return
        accounts = [str(a) for a in self.ib.managedAccounts()]
        if not accounts:
            return
        if expected == "paper":
            wrong = [a for a in accounts if not is_paper_account(a)]
        else:
            wrong = [a for a in accounts if is_paper_account(a)]
        if wrong:
            self.ib.disconnect()
            raise IBKRWrongAccountError(
                f"profile={expected} but {self.config.host}:{self.config.port} serves "
                f"account(s) {wrong}; refusing to continue. Check the port: "
                "paper is 4002 (Gateway) / 7497 (TWS), live is 4001 / 7496."
            )

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Disconnected from IBKR")

    def __enter__(self) -> "IBKRClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def ensure_connected(self) -> None:
        if not self.ib.isConnected():
            raise IBKRNotConnectedError("IBKR is not connected")

    @property
    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def sleep(self, seconds: float) -> None:
        self.ensure_connected()
        self.ib.sleep(seconds)

    def set_market_data_type(self, delayed: bool = False) -> None:
        """1 = live, 3 = delayed."""
        self.ensure_connected()
        self.ib.reqMarketDataType(3 if delayed else 1)

    def managed_accounts(self) -> list[str]:
        self.ensure_connected()
        return list(self.ib.managedAccounts())

    def primary_account(self) -> str:
        if self.config.account:
            return self.config.account

        accounts = self.managed_accounts()
        if not accounts:
            raise IBKRClientError("No managed IBKR accounts returned")
        return accounts[0]

    def account_summary(self, account: str | None = None) -> dict[str, str]:
        self.ensure_connected()
        acct = account or self.primary_account()
        summary = self.ib.accountSummary(acct)
        return {item.tag: item.value for item in summary}

    def account_value(self, tag: str, account: str | None = None) -> Optional[str]:
        return self.account_summary(account).get(tag)

    def net_liquidation(self, account: str | None = None) -> float:
        value = self.account_value("NetLiquidation", account)
        if value is None:
            raise IBKRClientError("NetLiquidation not returned by IBKR")
        return float(value)

    def available_funds(self, account: str | None = None) -> float:
        value = self.account_value("AvailableFunds", account)
        if value is None:
            raise IBKRClientError("AvailableFunds not returned by IBKR")
        return float(value)

    def positions(self, account: str | None = None) -> list[Any]:
        self.ensure_connected()
        positions = list(self.ib.positions())
        if account is None:
            return positions
        return [p for p in positions if getattr(p, "account", None) == account]

    def open_trades(self) -> list[Any]:
        self.ensure_connected()
        return list(self.ib.openTrades())

    def open_orders(self) -> list[Any]:
        self.ensure_connected()
        return list(self.ib.openOrders())

    def qualify_contract(self, contract: Any) -> Any:
        self.ensure_connected()
        qualified = self.ib.qualifyContracts(contract)
        # ib_async 2.x returns [None] (not []) for unknown / ambiguous contracts.
        if not qualified or qualified[0] is None:
            raise IBKRClientError(f"Failed to qualify contract: {contract}")
        return qualified[0]

    def qualify_stock(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Any:
        contract = Stock(symbol, exchange, currency)
        return self.qualify_contract(contract)

    def get_ticker(self, contract: Any, *, snapshot: bool = True) -> Any:
        self.ensure_connected()
        return self.ib.reqMktData(contract, "", snapshot, False)

    def get_last_price(
        self,
        contract: Any,
        *,
        snapshot: bool = True,
        timeout: float = 5.0,
    ) -> float:
        """Return a best-effort price from the IB ticker object.

        Price priority:
        last -> close -> marketPrice() -> midpoint(bid/ask) -> bid -> ask
        """
        self.ensure_connected()
        ticker = self.get_ticker(contract, snapshot=snapshot)

        deadline_reached = False
        elapsed = 0.0
        poll = 0.25
        while elapsed < timeout:
            price = self._extract_price(ticker)
            if price is not None:
                return price
            self.ib.sleep(poll)
            elapsed += poll
        deadline_reached = True

        if deadline_reached:
            price = self._extract_price(ticker)
            if price is not None:
                return price
        raise IBKRClientError(f"No market price returned for contract: {contract}")

    @staticmethod
    def _extract_price(ticker: Any) -> Optional[float]:
        candidates: list[Optional[float]] = []

        last = getattr(ticker, "last", None)
        close = getattr(ticker, "close", None)
        bid = getattr(ticker, "bid", None)
        ask = getattr(ticker, "ask", None)

        candidates.extend([last, close])

        market_price_attr = getattr(ticker, "marketPrice", None)
        if callable(market_price_attr):
            try:
                candidates.append(market_price_attr())
            except Exception:
                pass

        if bid is not None and ask is not None and bid > 0 and ask > 0:
            candidates.append((bid + ask) / 2.0)

        candidates.extend([bid, ask])

        for value in candidates:
            if value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if num > 0:
                return num
        return None
