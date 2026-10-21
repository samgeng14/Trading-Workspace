"""
record.py — Write the trading record to the store: runs, orders, fills,
positions and NAV.

Every writer is idempotent: the same input twice leaves the same rows, so a
backfill can run again and a retried job adds nothing. Each call is one
transaction. ``best_effort`` is how the daily run calls them: a store that is
off or down is logged, never raised, since the engine's files stay its record.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from src.storage.db import StoreConfig, connect, load_config

log = logging.getLogger(__name__)

RUN_ID = re.compile(r"^(\d{8})_(\d{6})$")
# IBKR's order states, as orders.status keeps them
ORDER_STATUS = {
    "Filled": "filled",
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
    "Inactive": "cancelled",
    "ERROR": "error",
}


def _jsonb(obj: Any):
    from psycopg.types.json import Jsonb

    return Jsonb(obj, dumps=lambda o: json.dumps(o, default=str))


def _num(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _int(value: Any) -> int | None:
    x = _num(value)
    return int(x) if x is not None and x != 0 else None


def run_started(run_id: str, tz: timezone | None = None) -> datetime | None:
    """The runner's run_id is its local start time, YYYYmmdd_HHMMSS; ``tz`` is that
    machine's zone (default this machine's)."""
    m = RUN_ID.match(str(run_id or ""))
    if not m:
        return None
    naive = datetime.strptime(f"{m.group(1)}{m.group(2)}", "%Y%m%d%H%M%S")
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


def _local_time(text: Any, tz: timezone | None) -> datetime | None:
    """A timestamp the engine wrote in local time without a zone (or with one)."""
    try:
        dt = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=tz) if tz else dt.astimezone()


# ── runs ──────────────────────────────────────────────────────────────────


def run_status(doc: Mapping[str, Any]) -> str:
    if doc.get("skipped"):
        return "skipped"
    if doc.get("halted"):
        return "halted"
    return "ok" if doc.get("ok") else "error"


def run_error(doc: Mapping[str, Any]) -> str | None:
    if doc.get("error"):
        return str(doc["error"])
    if doc.get("halted"):
        return f"kill switch armed: {doc['halted']}"
    submit = (doc.get("steps") or {}).get("submit_orders") or {}
    if submit.get("blocked"):
        return "; ".join(map(str, submit.get("reasons") or [])) or "blocked"
    return None


def record_run(
    conn,
    doc: Mapping[str, Any],
    *,
    job: str = "daily_run",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> None:
    """One daily run's log (artifacts/runs/daily_run_<run_id>.json) as a run_logs row.
    ``ended_at`` is when the log was written; without it the end and the duration
    stay empty."""
    run_id = str(doc.get("run_id") or "")
    started = started_at or run_started(run_id) or datetime.now(timezone.utc)
    duration_ms = max(0, int((ended_at - started).total_seconds() * 1000)) if ended_at else None
    conn.execute(
        """
        INSERT INTO run_logs (job, profile, status, started_at, ended_at, duration_ms, error, payload, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (job, profile, run_id) DO UPDATE SET
            status = EXCLUDED.status, started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at,
            duration_ms = EXCLUDED.duration_ms, error = EXCLUDED.error, payload = EXCLUDED.payload
        """,
        (job, str(doc.get("profile") or "paper"), run_status(doc), started, ended_at, duration_ms, run_error(doc),
         _jsonb(dict(doc)), run_id),
    )  # fmt: skip


# ── orders ────────────────────────────────────────────────────────────────


def submission_batch(path: Any) -> str | None:
    """A submission log's own stamp, YYYYmmdd_HHMMSS, from its name
    (paper_orders_submitted_20261016_003535.csv): when the batch was sent."""
    parts = str(Path(str(path)).stem).rsplit("_", 2)
    stamp = f"{parts[-2]}_{parts[-1]}" if len(parts) == 3 else ""
    return stamp if RUN_ID.match(stamp) else None


def order_key(profile: str, batch: str, symbol: str, side: str, perm_id: int | None) -> str:
    """IBKR's permId when the order reached IBKR (unique across clients and days),
    else the batch, symbol and side: a batch never sends two orders for one of those."""
    return f"{profile}:perm:{perm_id}" if perm_id else f"{profile}:{batch}:{symbol}:{side}"


def record_submission(
    conn,
    profile: str,
    batch: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    tz: timezone | None = None,
) -> int:
    """A submission log (submit_paper_orders' paper_orders_submitted_<batch>.csv rows)
    as orders rows; orders.run_id is the batch as a number. A status the fills already
    set (filled, partial) is kept, and so is what a reconcile saw. The daily run and
    the backfill read the same file, so they write the same rows."""
    m = RUN_ID.match(str(batch or ""))
    run_number = int(m.group(1) + m.group(2)) if m else None
    n = 0
    with conn.transaction():
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            side = str(row.get("action") or row.get("side") or "").upper().strip()
            qty = abs(_num(row.get("quantity")) or 0.0)
            if not symbol or side not in ("BUY", "SELL") or not qty:
                continue
            dollars = _num(row.get("estimated_trade_dollars"))
            raw_status = str(row.get("status") or "")
            error = row.get("error")
            perm = _int(row.get("perm_id"))
            conn.execute(
                """
                INSERT INTO orders (idempotency_key, run_id, profile, symbol, side, qty, price, order_type, status,
                                    broker_order_id, broker_perm_id, submitted_at, payload, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO UPDATE SET
                    status = CASE WHEN orders.status IN ('filled', 'partial') THEN orders.status ELSE EXCLUDED.status END,
                    broker_order_id = EXCLUDED.broker_order_id, broker_perm_id = EXCLUDED.broker_perm_id,
                    price = EXCLUDED.price, error = EXCLUDED.error, updated_at = now(),
                    payload = CASE WHEN orders.payload ? 'reconciled'
                                   THEN EXCLUDED.payload || jsonb_build_object('reconciled', orders.payload->'reconciled')
                                   ELSE EXCLUDED.payload END
                """,
                (order_key(profile, batch, symbol, side, perm), run_number, profile, symbol, side, int(qty),
                 dollars / qty if dollars else None, str(row.get("order_type") or "MKT"),
                 ORDER_STATUS.get(raw_status, "submitted"), _int(row.get("order_id")), perm,
                 _local_time(row.get("timestamp"), tz) or datetime.now(timezone.utc), _jsonb(dict(row)),
                 str(error) if error and str(error) != "nan" else None),
            )  # fmt: skip
            n += 1
    return n


# ── fills ─────────────────────────────────────────────────────────────────


def record_fills(conn, profile: str, fills: Iterable[Mapping[str, Any]]) -> int:
    """IBKR executions (src/broker/ibkr/executions.py's dicts: exec_id, perm_id,
    order_id, symbol, signed qty, price, side, ts) as fills rows, each linked to its
    order by permId; then each order's status from its fills: filled or partial."""
    added, perms = 0, set()
    with conn.transaction():
        for f in fills:
            exec_id = str(f.get("exec_id") or "")
            qty, price = _num(f.get("qty")), _num(f.get("price"))
            filled_at = _local_time(f.get("ts"), timezone.utc)
            if not exec_id or not qty or price is None or filled_at is None:
                continue
            perm = _int(f.get("perm_id"))
            cur = conn.execute(
                """
                INSERT INTO fills (exec_id, order_id, broker_order_id, broker_perm_id, profile, symbol, side, qty,
                                   price, filled_at, payload)
                VALUES (%s, (SELECT id FROM orders WHERE broker_perm_id = %s AND profile = %s
                             ORDER BY submitted_at DESC LIMIT 1),
                        %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exec_id) DO NOTHING
                """,
                (exec_id, perm, profile, _int(f.get("order_id")), perm, profile, str(f.get("symbol") or "").upper(),
                 str(f.get("side") or "") or None, qty, price, filled_at, _jsonb(dict(f))),
            )  # fmt: skip
            added += cur.rowcount
            if perm:
                perms.add(perm)
        if perms:
            conn.execute(
                """
                UPDATE orders SET status = CASE WHEN f.filled >= orders.qty THEN 'filled' ELSE 'partial' END,
                                  updated_at = now()
                FROM (SELECT broker_perm_id, SUM(ABS(qty)) AS filled FROM fills
                      WHERE broker_perm_id = ANY(%s) GROUP BY broker_perm_id) AS f
                WHERE orders.broker_perm_id = f.broker_perm_id AND orders.profile = %s
                  AND orders.status NOT IN ('cancelled', 'error')
                """,
                (sorted(perms), profile),
            )
    return added


def record_reconciled_orders(conn, profile: str, orders: Iterable[Mapping[str, Any]]) -> int:
    """Orders' fills as an end-of-day reconcile saw them (its report's "orders":
    perm_id, quantity, filled, price), for orders whose executions the store never
    saw: IBKR's API shows only the current day's. Sets filled or partial and keeps
    what the reconcile saw in the payload; adds no fills rows (it had no exec IDs)."""
    n = 0
    with conn.transaction():
        for o in orders:
            perm = _int(o.get("perm_id"))
            qty, filled = abs(_num(o.get("quantity")) or 0.0), abs(_num(o.get("filled")) or 0.0)
            if not perm or not filled:
                continue
            seen = {"filled": filled, "price": _num(o.get("price")), "fills_at": o.get("fills_at")}
            cur = conn.execute(
                """
                UPDATE orders SET status = %s, updated_at = now(),
                                  payload = COALESCE(payload, '{}'::jsonb) || jsonb_build_object('reconciled', %s::jsonb)
                WHERE profile = %s AND broker_perm_id = %s AND status NOT IN ('filled', 'cancelled', 'error')
                """,
                ("filled" if filled >= qty else "partial", _jsonb(seen), profile, perm),
            )  # fmt: skip
            n += cur.rowcount
    return n


# ── positions and NAV ─────────────────────────────────────────────────────


def record_positions(conn, profile: str, snapshot_ts: datetime, rows: Iterable[Mapping[str, Any]]) -> int:
    """One snapshot of every holding (build_paper_basket.fetch_positions_df's rows, or
    {symbol, current_shares} alone), keyed by its time."""
    n = 0
    with conn.transaction():
        for row in rows:
            qty = _num(row.get("current_shares", row.get("qty")))
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or not qty:
                continue
            mark = _num(row.get("market_price"))
            multiplier = _num(row.get("multiplier")) or 1.0
            cur = conn.execute(
                """
                INSERT INTO positions (snapshot_ts, profile, symbol, qty, avg_cost, market_value, unrealized_pnl)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_ts, profile, symbol) DO NOTHING
                """,
                (snapshot_ts, profile, symbol, qty, _num(row.get("avg_cost")),
                 qty * mark * multiplier if mark and mark > 0 else None, _num(row.get("unrealized_pnl"))),
            )  # fmt: skip
            n += cur.rowcount
    return n


def record_nav(
    conn, profile: str, day: date, nav: float, *, source: str, cash: float | None = None
) -> None:
    """The NAV at ``day``'s close from ``source`` (flex, api_close, ibkr_close, ...);
    a later value from the same source replaces it."""
    conn.execute(
        """
        INSERT INTO nav (profile, nav_date, nav, cash, source) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (profile, nav_date, source) DO UPDATE SET
            nav = EXCLUDED.nav, cash = COALESCE(EXCLUDED.cash, nav.cash)
        """,
        (profile, day, float(nav), cash, source),
    )


# ── for the daily run ─────────────────────────────────────────────────────


def best_effort(what: str, write: Callable[[Any], Any], cfg: StoreConfig | None = None) -> bool:
    """Run ``write(conn)`` against the store when it is on; True when it wrote. A
    failure is logged and swallowed: the run goes on and the files keep the record."""
    where = "the store"
    try:
        cfg = cfg or load_config()
        if not cfg.enabled:
            return False
        where = cfg.where
        with connect(cfg) as conn:
            write(conn)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "store: couldn't record %s at %s (%s: %s); the engine's files still have it",
            what, where, type(exc).__name__, exc,
        )  # fmt: skip
        return False


def zone_from(run_id: str, written: datetime) -> timezone | None:
    """The zone a run_id was stamped in, from when its file was last written
    (``written``, aware): runs take minutes, so the gap rounds to the zone's offset.
    The Mac's runs are in Central time, the VM's in UTC. None when the gap is no
    zone's (a file copied without its times, say) or the run_id isn't one."""
    naive = run_started(run_id, timezone.utc)
    if naive is None:
        return None
    hours = round((written - naive).total_seconds() / 3600)
    if not -14 <= hours <= 12:  # zones run from UTC-12 to UTC+14
        return None
    return timezone(timedelta(hours=-hours))
