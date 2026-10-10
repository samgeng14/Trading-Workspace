from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Literal, Optional

from ib_async import Contract, LimitOrder, MarketOrder, Order, Stock, StopOrder

from .client import IBKRClient

logger = logging.getLogger(__name__)

OrderAction = Literal["BUY", "SELL"]
OrderType = Literal["MKT", "LMT", "STP", "MOC"]


class IBKROrderError(RuntimeError):
    """Base error for order construction and submission."""


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    action: OrderAction
    quantity: float
    order_type: OrderType = "MKT"
    limit_price: float | None = None
    stop_price: float | None = None
    exchange: str = "SMART"
    currency: str = "USD"
    tif: str = "DAY"
    outside_rth: bool = False
    account: str | None = None
    order_ref: str | None = None

    def normalized_symbol(self) -> str:
        return self.symbol.strip().upper()

    def validate(self) -> None:
        if self.action not in {"BUY", "SELL"}:
            raise IBKROrderError(f"Unsupported action: {self.action}")
        if self.quantity <= 0:
            raise IBKROrderError("Quantity must be positive")
        if self.order_type == "LMT" and (self.limit_price is None or self.limit_price <= 0):
            raise IBKROrderError("Limit orders require a positive limit_price")
        if self.order_type == "STP" and (self.stop_price is None or self.stop_price <= 0):
            raise IBKROrderError("Stop orders require a positive stop_price")
        if self.order_type == "MOC" and self.tif != "DAY":
            raise IBKROrderError("MOC orders must use tif='DAY'")


class IBKROrderManager:
    def __init__(self, client: IBKRClient) -> None:
        self.client = client

    def build_stock_contract(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
        *,
        qualify: bool = True,
    ) -> Contract:
        contract = Stock(symbol.strip().upper(), exchange, currency)
        if qualify:
            return self.client.qualify_contract(contract)
        return contract

    def build_order(self, request: OrderRequest) -> Order:
        request.validate()
        account = request.account or self.client.primary_account()

        if request.order_type == "MKT":
            order = MarketOrder(request.action, request.quantity)
        elif request.order_type == "LMT":
            order = LimitOrder(request.action, request.quantity, request.limit_price)
        elif request.order_type == "STP":
            order = StopOrder(request.action, request.quantity, request.stop_price)
        elif request.order_type == "MOC":
            order = Order(
                action=request.action,
                totalQuantity=request.quantity,
                orderType="MOC",
                tif="DAY",
            )
        else:
            raise IBKROrderError(f"Unsupported order_type: {request.order_type}")

        order.account = account
        order.outsideRth = request.outside_rth
        order.tif = "DAY" if request.order_type == "MOC" else request.tif
        if request.order_ref:
            order.orderRef = request.order_ref
        return order

    def place_order(
        self,
        request: OrderRequest,
        *,
        qualify: bool = True,
        wait_for_status: bool = False,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> Any:
        contract = self.build_stock_contract(
            symbol=request.normalized_symbol(),
            exchange=request.exchange,
            currency=request.currency,
            qualify=qualify,
        )
        order = self.build_order(request)

        logger.info(
            "Placing order symbol=%s action=%s qty=%s type=%s account=%s",
            request.normalized_symbol(),
            request.action,
            request.quantity,
            request.order_type,
            order.account,
        )
        trade = self.client.ib.placeOrder(contract, order)

        if wait_for_status:
            self.wait_for_trade_update(
                trade,
                timeout=timeout,
                poll_interval=poll_interval,
            )
        return trade

    def place_stock_order(
        self,
        symbol: str,
        action: OrderAction,
        quantity: float,
        *,
        order_type: OrderType = "MKT",
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: str = "DAY",
        outside_rth: bool = False,
        account: str | None = None,
        order_ref: str | None = None,
        wait_for_status: bool = False,
        timeout: float = 30.0,
    ) -> Any:
        request = OrderRequest(
            symbol=symbol,
            action=action,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            tif=tif,
            outside_rth=outside_rth,
            account=account,
            order_ref=order_ref,
        )
        return self.place_order(
            request,
            wait_for_status=wait_for_status,
            timeout=timeout,
        )

    def place_batch(
        self,
        requests: list[OrderRequest],
        *,
        wait_for_status: bool = False,
        timeout: float = 30.0,
    ) -> list[Any]:
        trades = []
        for request in requests:
            trades.append(
                self.place_order(
                    request,
                    wait_for_status=wait_for_status,
                    timeout=timeout,
                )
            )
        return trades

    def cancel_trade(self, trade: Any) -> Any:
        logger.info(
            "Cancelling trade orderId=%s symbol=%s",
            getattr(getattr(trade, "order", None), "orderId", None),
            getattr(getattr(trade, "contract", None), "symbol", None),
        )
        return self.client.ib.cancelOrder(trade.order)

    def cancel_open_orders(self, symbol: str | None = None) -> list[Any]:
        cancelled = []
        for trade in self.client.open_trades():
            trade_symbol = getattr(getattr(trade, "contract", None), "symbol", None)
            if symbol is not None and trade_symbol != symbol.strip().upper():
                continue
            cancelled.append(self.cancel_trade(trade))
        return cancelled

    def wait_for_trade_done(
        self,
        trade: Any,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> str:
        elapsed = 0.0
        while elapsed < timeout:
            if trade.isDone():
                return trade.orderStatus.status
            self.client.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(
            f"Trade did not finish within {timeout:.1f}s; "
            f"last_status={trade.orderStatus.status}"
        )

    def wait_for_trade_update(
        self,
        trade: Any,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> str:
        elapsed = 0.0
        while elapsed < timeout:
            status = getattr(trade.orderStatus, "status", None)
            if status not in {None, "", "PendingSubmit", "ApiPending"}:
                return status
            self.client.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(
            f"Trade received no useful status within {timeout:.1f}s; "
            f"last_status={trade.orderStatus.status}"
        )

    @staticmethod
    def summarize_trade(trade: Any) -> dict[str, Any]:
        fills = getattr(trade, "fills", []) or []
        filled_qty = sum(getattr(fill.execution, "shares", 0) for fill in fills)
        avg_fill_price = None
        if fills:
            notional = sum(
                getattr(fill.execution, "shares", 0) * getattr(fill.execution, "price", 0.0)
                for fill in fills
            )
            if filled_qty:
                avg_fill_price = notional / filled_qty

        return {
            "symbol": getattr(getattr(trade, "contract", None), "symbol", None),
            "order_id": getattr(getattr(trade, "order", None), "orderId", None),
            "perm_id": getattr(getattr(trade, "order", None), "permId", None),
            "action": getattr(getattr(trade, "order", None), "action", None),
            "order_type": getattr(getattr(trade, "order", None), "orderType", None),
            "quantity": getattr(getattr(trade, "order", None), "totalQuantity", None),
            "status": getattr(getattr(trade, "orderStatus", None), "status", None),
            "filled_qty": filled_qty,
            "avg_fill_price": avg_fill_price,
        }


# ── Module-level convenience for the order_router ─────────────────────────
#
# ``src.execution.order_router`` calls ``ibkr_orders.submit_one(ticket)``
# with a single ``OrderTicket`` (symbol, signed qty, price, side). The
# router persists the returned dict — so this function must:
#   * always return a dict (never raise),
#   * mark ok=False with an error string on any failure, and
#   * include broker identifiers (order_id, perm_id, status) on success.
def submit_one(
    ticket: Any,
    *,
    profile: str = "paper",
    manager: "IBKROrderManager | None" = None,
    order_type: OrderType = "MKT",
    wait_for_status: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Submit a single ``OrderTicket`` to IBKR.

    Always returns a result dict. Designed to be called from
    ``order_router.submit_orders``; never raises so the router's audit
    log captures every failure mode.

    Parameters
    ----------
    ticket : OrderTicket
        Must expose ``symbol`` (str), ``qty`` (signed int), ``price``
        (float). Sign of ``qty`` chooses BUY / SELL.
    profile : "paper" or "live"
    manager : optional injected ``IBKROrderManager`` (tests use this
              to avoid the network).
    order_type : "MKT" / "LMT" / "STP" / "MOC"
    """
    sym: str | None = None
    try:
        sym = str(getattr(ticket, "symbol")).strip().upper()
        qty_signed = int(getattr(ticket, "qty"))
        if not sym:
            return {"ok": False, "error": "missing symbol", "symbol": sym}
        if qty_signed == 0:
            return {"ok": False, "error": "zero quantity", "symbol": sym}

        action: OrderAction = "BUY" if qty_signed > 0 else "SELL"
        request = OrderRequest(
            symbol=sym,
            action=action,
            quantity=float(abs(qty_signed)),
            order_type=order_type if order_type in {"MKT", "LMT", "STP", "MOC"} else "MKT",
            limit_price=float(getattr(ticket, "price")) if order_type == "LMT" else None,
            order_ref=f"{profile}-router-{sym}-{abs(qty_signed)}",
        )
    except Exception as exc:  # noqa: BLE001 — bad ticket → audit row, not crash
        return {"ok": False, "error": f"ticket build failed: {exc}", "symbol": sym}

    owns_manager = manager is None
    if manager is None:
        from .settings import make_client

        try:
            client = make_client(profile, "orders")
            client.connect()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"connect failed: {exc}", "symbol": sym}
        manager = IBKROrderManager(client)

    try:
        trade = manager.place_order(
            request,
            wait_for_status=wait_for_status,
            timeout=timeout,
        )
        summary = IBKROrderManager.summarize_trade(trade)
        return {
            "ok": True,
            "symbol": sym,
            "action": action,
            "qty": int(abs(qty_signed)),
            "order_id": summary.get("order_id"),
            "perm_id": summary.get("perm_id"),
            "status": summary.get("status"),
            "filled_qty": summary.get("filled_qty"),
            "avg_fill_price": summary.get("avg_fill_price"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("submit_one: place failed for %s", sym)
        return {"ok": False, "error": str(exc), "symbol": sym}
    finally:
        if owns_manager:
            try:
                manager.client.disconnect()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "OrderRequest",
    "IBKROrderError",
    "IBKROrderManager",
    "submit_one",
]
