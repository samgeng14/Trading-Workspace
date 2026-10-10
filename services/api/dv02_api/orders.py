"""Orders for the blotter: live from IBKR, else the engine's last submission files."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import engine

STATUS = {
    "PendingSubmit": "pending",
    "ApiPending": "pending",
    "PreSubmitted": "submitted",
    "Submitted": "submitted",
    "Filled": "filled",
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
    "PendingCancel": "cancelled",
    "Inactive": "rejected",
}
ORDER_TYPES = {"MKT", "LMT", "MOC", "LOC"}


def order_id(trade: Any) -> str:
    o = trade.order
    return str(o.permId) if o.permId else f"{o.clientId}-{o.orderId}"


def _iso(dt: Any) -> str | None:
    if not isinstance(dt, datetime):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat(timespec="seconds")


def from_trade(trade: Any, sleeve: str) -> dict[str, Any]:
    o, s, c = trade.order, trade.orderStatus, trade.contract
    filled = float(s.filled or 0)
    status = STATUS.get(s.status, "submitted")
    if status == "submitted" and filled > 0:
        status = "partial"
    times = [e.time for e in trade.log if getattr(e, "time", None)]
    fill_times = [f.time for f in trade.fills if getattr(f, "time", None)]
    submitted = _iso(times[0] if times else fill_times[0] if fill_times else None)
    updated = _iso(times[-1] if times else fill_times[-1] if fill_times else None)
    out: dict[str, Any] = {
        "id": order_id(trade),
        "symbol": c.localSymbol if c.secType == "FUT" and c.localSymbol else c.symbol,
        "side": "BUY" if o.action == "BUY" else "SELL",
        "quantity": float(o.totalQuantity),
        "filled": filled,
        "order_type": o.orderType if o.orderType in ORDER_TYPES else ("LMT" if o.lmtPrice else "MKT"),
        "status": status,
        "submitted_at": submitted or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sleeve": "Hedge" if c.secType == "FUT" else sleeve,
    }
    if o.orderType in ("LMT", "LOC") and o.lmtPrice and o.lmtPrice < 1e300:
        out["limit_price"] = float(o.lmtPrice)
    if s.avgFillPrice:
        out["avg_fill_price"] = float(s.avgFillPrice)
    if updated:
        out["updated_at"] = updated
    reason = o.orderRef or (trade.log[-1].message if trade.log and trade.log[-1].message else "")
    if reason:
        out["reason"] = reason
    return out


def _latest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


def from_files(profile: str, sleeve: str) -> tuple[str | None, list[dict[str, Any]]]:
    """The engine's last real submission and its fill snapshot (trading date, orders)."""
    folder = engine.data_dir() / "broker" / "orders"
    sub = _latest(folder, f"{profile}_orders_submitted_*.csv")
    if sub is None:
        return None, []
    fills_path = engine.data_dir() / "broker" / "fills" / sub.name.replace("orders_submitted", "trade_log")
    fills: dict[str, dict[str, str]] = {}
    if fills_path.exists():
        with fills_path.open() as f:
            fills = {r.get("perm_id") or r.get("order_id", ""): r for r in csv.DictReader(f)}
    orders, day = [], None
    with sub.open() as f:
        for i, r in enumerate(csv.DictReader(f)):
            if r.get("status") == "DRY_RUN":
                continue
            ts = r.get("timestamp") or ""
            try:
                when = datetime.fromisoformat(ts).astimezone()
            except ValueError:
                when = datetime.fromtimestamp(sub.stat().st_mtime).astimezone()
            day = when.date().isoformat()
            key = r.get("perm_id") or r.get("order_id") or f"file-{i}"
            fill = fills.get(key, {})
            raw = (fill.get("status") or r.get("status") or "").strip()
            filled = float(fill.get("filled_qty") or 0)
            order: dict[str, Any] = {
                "id": str(key),
                "symbol": r.get("symbol", "?"),
                "side": "BUY" if r.get("action") == "BUY" else "SELL",
                "quantity": float(r.get("quantity") or 0),
                "filled": filled,
                "order_type": r.get("order_type") if r.get("order_type") in ORDER_TYPES else "MKT",
                "status": "rejected" if raw == "ERROR" else STATUS.get(raw, "submitted"),
                "submitted_at": when.isoformat(timespec="seconds"),
                "sleeve": sleeve,
                "reason": "from the engine's submission log (IBKR not connected)",
            }
            if fill.get("avg_fill_price"):
                try:
                    order["avg_fill_price"] = float(fill["avg_fill_price"])
                except ValueError:
                    pass
            if r.get("error"):
                order["reason"] = r["error"]
            orders.append(order)
    return day, orders
