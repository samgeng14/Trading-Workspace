"""
executions.py — Read recent fills/executions from IBKR.

Thin reader. ``execution.fill_monitor.list_recent_fills`` calls
``list_executions(profile, since)`` and expects an iterable of dicts
with at minimum: order_id, symbol, qty, price, ts.

Connection is opened on demand (paper profile by default) and closed
on exit, so the function is safe to call from short-lived jobs. Tests
inject a fake client to avoid the network.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .client import IBKRClient
from .settings import make_client

log = logging.getLogger(__name__)


def _make_readonly_client(profile: str) -> IBKRClient:
    # Forced read-only: list_executions never writes.
    return make_client(profile, "executions", readonly=True)


def _normalize_fill(fill: Any) -> dict[str, Any] | None:
    """Coerce one ``ib_async.Fill`` into our flat dict shape, or None on bad input."""
    try:
        execution = getattr(fill, "execution", None)
        contract = getattr(fill, "contract", None)
        if execution is None or contract is None:
            return None

        symbol = getattr(contract, "symbol", None)
        shares = getattr(execution, "shares", None)
        price = getattr(execution, "price", None)
        if symbol is None or shares is None or price is None:
            return None

        # Sign the qty so downstream code can sum cumulatively.
        side = getattr(execution, "side", "") or ""
        try:
            qty = int(round(float(shares)))
        except (TypeError, ValueError):
            return None
        if isinstance(side, str) and side.strip().upper().startswith("S"):
            qty = -qty

        ts_raw = getattr(execution, "time", None)
        if isinstance(ts_raw, datetime):
            ts_iso = ts_raw.isoformat()
        elif ts_raw is not None:
            ts_iso = str(ts_raw)
        else:
            ts_iso = datetime.now(timezone.utc).isoformat()

        order_id = getattr(execution, "orderId", None)
        perm_id = getattr(execution, "permId", None)
        return {
            "order_id": str(order_id) if order_id is not None else "",
            # unique across clients and days, unlike orderId: how a fill finds its order
            "perm_id": str(perm_id) if perm_id else "",
            "exec_id": str(getattr(execution, "execId", "") or ""),
            "symbol": str(symbol).strip().upper(),
            "qty": qty,
            "price": float(price),
            "side": str(side),
            "ts": ts_iso,
        }
    except Exception as exc:  # noqa: BLE001 — never blow up on a single bad fill
        log.warning("executions: failed to normalize fill: %s", exc)
        return None


def _parse_iso_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None on failure."""
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def list_executions(
    *,
    profile: str = "paper",
    since: datetime | None = None,
    client: IBKRClient | None = None,
) -> list[dict[str, Any]]:
    """Return executions for ``profile`` since ``since``.

    Pulls via ``IB.fills()``. The caller may inject a connected
    ``client`` for tests or to share an existing connection. If
    ``client`` is None we open a short-lived read-only connection,
    drain fills, and disconnect.

    On any connection failure we log and return ``[]`` rather than
    raising — fill_monitor treats this module as best-effort and the
    EOD job has its own retry path.
    """
    owns_client = client is None
    if client is None:
        try:
            client = _make_readonly_client(profile)
            client.connect()
        except Exception as exc:  # noqa: BLE001 — broker down ≠ caller's problem
            log.warning("executions: IBKR connect failed (%s); returning []", exc)
            return []

    try:
        try:
            raw_fills = list(client.ib.fills())
        except Exception as exc:  # noqa: BLE001
            log.warning("executions: ib.fills() failed: %s", exc)
            return []
    finally:
        if owns_client:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    out: list[dict[str, Any]] = []
    for fill in raw_fills:
        norm = _normalize_fill(fill)
        if norm is None:
            continue
        if since is not None:
            fill_ts = _parse_iso_ts(norm["ts"])
            if fill_ts is not None:
                # Reconcile naive/aware mismatches before comparing.
                if fill_ts.tzinfo is not None and since.tzinfo is None:
                    fill_ts = fill_ts.replace(tzinfo=None)
                elif fill_ts.tzinfo is None and since.tzinfo is not None:
                    fill_ts = fill_ts.replace(tzinfo=since.tzinfo)
                if fill_ts < since:
                    continue
        out.append(norm)
    return out


__all__ = ["list_executions"]
