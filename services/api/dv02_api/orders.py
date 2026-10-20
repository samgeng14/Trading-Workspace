"""Orders for the blotter: live from IBKR, else the engine's last submission files."""

from __future__ import annotations

import csv
import json
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


# IBKR's "no value" for a double field (ib_async's UNSET_DOUBLE)
UNSET = 1e300


def _qty(v: Any) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if abs(f) >= UNSET else f


def from_trade(trade: Any, sleeve: str) -> dict[str, Any]:
    o, s, c = trade.order, trade.orderStatus, trade.contract
    # a completed order (reqCompletedOrders) carries its fill on the order, not in a status message
    filled = _qty(s.filled) or _qty(getattr(o, "filledQuantity", 0))
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


def reconcile_fills(profile: str, day: str | None = None) -> dict[str, dict[str, Any]]:
    """The fills IBKR reported, by perm ID, from the end-of-day reconcile's report
    (data/broker/reconciliations/{profile}_eod_{day}.json, the latest when ``day`` is None).
    The reconcile counts every order at the fills IBKR reports, so a machine without an IBKR
    connection can still say what filled."""
    folder = engine.data_dir() / "broker" / "reconciliations"
    path = folder / f"{profile}_eod_{day}.json" if day else _latest(folder, f"{profile}_eod_*.json")
    if path is None or not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for o in doc.get("orders") or []:
        key = str(o.get("perm_id") or o.get("order_id") or "")
        if not key or "at the fills IBKR reports" not in str(o.get("counted", "")):
            continue  # an order counted in full only because IBKR reported nothing is not a known fill
        out[key] = {
            "filled": abs(_qty(o.get("filled"))),
            "quantity": abs(_qty(o.get("quantity"))),
            "price": o.get("price"),
            "fills_at": o.get("fills_at"),
            "run_id": o.get("run_id"),
        }
    return out


def from_files(profile: str, sleeve: str) -> tuple[str | None, list[dict[str, Any]]]:
    """The engine's last real submission and its fill snapshot (trading date, orders). The
    snapshot is taken when the orders are sent; the end-of-day reconcile's fills, when there
    are some, say what filled later."""
    folder = engine.data_dir() / "broker" / "orders"
    sub = _latest(folder, f"{profile}_orders_submitted_*.csv")
    if sub is None:
        return None, []
    fills_path = engine.data_dir() / "broker" / "fills" / sub.name.replace("orders_submitted", "trade_log")
    fills: dict[str, dict[str, str]] = {}
    if fills_path.exists():
        with fills_path.open() as f:
            fills = {r.get("perm_id") or r.get("order_id", ""): r for r in csv.DictReader(f)}
    reconciled = reconcile_fills(profile)
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
            later = reconciled.get(str(key))
            if later and later["filled"] > filled:
                filled = later["filled"]
                raw = "Filled" if filled >= float(r.get("quantity") or 0) else raw
                if later.get("price"):
                    fill = {**fill, "avg_fill_price": str(later["price"])}
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
                "reason": "from the engine's submission log and reconcile (IBKR not connected)",
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
