"""
end_of_day_reconcile.py — Did the engine's orders leave the account where it expected?

The daily runner runs this as its first step, for the orders earlier runs sent
(src/production/daily_runner.py), and ``python -m src.cli.reconcile`` runs it
by hand. It compares two sets of positions, to the share:

  * expected: the positions the basket was built on (the positions snapshot,
    recorded with each submission) plus every order sent since, at its fills.
    IBKR's API reports the current day's fills only, so an order with no fill
    reported counts in full, and the report says which orders those were.
  * actual: every position IBKR holds now, keyed as the snapshot keys them
    (futures and options by local symbol).

Each difference is a break. A break worth ``material_threshold_usd`` or more,
or one with no price to value it, arms the kill switch, so the pre-trade gate
blocks every order until someone has looked and disarmed it. IBKR's positions
then become the expected book, so each break is reported once.

The expected book is ``data/broker/reconciliations/{profile}_expected_book.json``.
``record_submission`` adds each submission to it and each reconcile resets it.
Before the first submission there is no book and nothing to reconcile. While
an order in it has yet to reach its auction (a market-on-close order before
that close) the reconcile waits for a later run.

The report goes next to it, ``{profile}_eod_{as_of}.json``. An unreachable
gateway fails the reconcile (ok False): it proves nothing about the book.
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.execution.kill_switch import (
    KillSwitchCorrupted,
    arm_kill_switch,
    is_kill_switch_armed,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECON_DIR = PROJECT_ROOT / "data" / "broker" / "reconciliations"

# Quantities closer than this are equal (IBKR allows fractional shares).
SHARE_TOLERANCE = 1e-6
# Statuses of an order that never reached IBKR's book: an error placing it, or
# cancelled on the spot. Anything else counts toward the expected positions.
NOT_PLACED = {"ERROR", "Cancelled", "ApiCancelled"}
# IBKR's positions show an auction's fills shortly after it; wait this long.
SETTLE = timedelta(minutes=30)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class EodReconcileResult:
    ok: bool
    profile: str
    as_of: str
    diffs: list[dict[str, Any]]
    material_mismatch: bool
    report_path: str | None
    error: str | None = None
    skipped: str | None = None  # why nothing was compared
    orders: list[dict[str, Any]] = field(default_factory=list)  # each reconciled order and how it counted

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str, sort_keys=True)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── the expected book ─────────────────────────────────────────────────────


def _recon_dir(root: Path | None) -> Path:
    return RECON_DIR if root is None else root / "data" / "broker" / "reconciliations"


def book_path(profile: str, root: Path | None = None) -> Path:
    """Where ``profile``'s expected book lives; ``root`` is the engine root (default this checkout)."""
    return _recon_dir(root) / f"{profile}_expected_book.json"


def load_expected_book(profile: str, root: Path | None = None) -> dict[str, Any] | None:
    """The expected book, or None before the first submission. Raises ValueError,
    naming the file, when it can't be read: a reconcile must not guess."""
    path = book_path(profile, root)
    if not path.exists():
        return None
    try:
        book = json.loads(path.read_text(encoding="utf-8"))
        book["positions"] = {str(s): float(q) for s, q in book["positions"].items()}
        book["unit_values"] = {str(s): float(v) for s, v in (book.get("unit_values") or {}).items()}
        for order in book["orders"]:
            order["symbol"] = str(order["symbol"])
            order["quantity"] = float(order["quantity"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"expected book {path} is unreadable ({type(exc).__name__}: {exc}); "
            "delete it and the next submission starts a new one"
        ) from exc
    return book


def _positive(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def _id(value: Any) -> str | None:
    """An IBKR order or perm ID as text; None when missing (or 0, IBKR's "none")."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return None if text in ("", "0", "nan", "None") else text


def _held(positions: pd.DataFrame) -> tuple[dict[str, float], dict[str, float]]:
    """``{symbol: quantity}`` of a positions frame (build_paper_basket.fetch_positions_df's
    columns), and ``{symbol: dollars per unit}`` where IBKR had a mark: the mark
    times the multiplier (1 for a share IBKR gives none)."""
    from src.production.runtime.build_paper_basket import SHARES

    held: dict[str, float] = {}
    values: dict[str, float] = {}
    for row in positions.to_dict("records"):
        try:
            qty = float(row.get("current_shares") or 0.0)
        except (TypeError, ValueError):
            qty = math.nan
        symbol = str(row["symbol"]).upper().strip()
        if not math.isfinite(qty):
            raise ValueError(f"position {symbol} has no quantity: {row.get('current_shares')!r}")
        if abs(qty) <= SHARE_TOLERANCE:
            continue
        held[symbol] = held.get(symbol, 0.0) + qty
        sec_type = row.get("sec_type") if isinstance(row.get("sec_type"), str) else ""
        mark = _positive(row.get("market_price"))
        multiplier = _positive(row.get("multiplier")) or (1.0 if sec_type in SHARES else None)
        if mark and multiplier:
            values[symbol] = mark * multiplier
    return held, values


def record_submission(
    profile: str,
    *,
    run_id: str,
    positions: pd.DataFrame,
    orders: pd.DataFrame,
    execution: Mapping[str, Any] | None = None,
    submitted_at: datetime | None = None,
    root: Path | None = None,
) -> Path:
    """Add a submission to the expected book.

    ``positions`` is the snapshot the basket was built on; ``orders`` is the
    submission log submit_paper_orders wrote (symbol, action, quantity, status,
    order_id, perm_id, estimated_trade_dollars); ``execution`` is the batch's
    auction (src/broker/ibkr/auction.py, ``Execution.as_dict``). With no orders
    pending, the book starts again from ``positions``. With some, it keeps its
    positions, which predate every order since.
    """
    submitted_at = submitted_at or datetime.now(timezone.utc).astimezone()
    execution = execution or {}
    path = book_path(profile, root)
    book = load_expected_book(profile, root)
    if book is None or not book["orders"]:
        held, values = _held(positions)
        book = {
            "profile": profile,
            "positions_at": submitted_at.isoformat(timespec="seconds"),
            "positions_from": f"the positions snapshot of run {run_id}",
            "positions": held,
            "unit_values": values,
            "orders": [],
        }
    for row in orders.to_dict("records"):
        qty = abs(float(row["quantity"]))
        action = str(row.get("action") or row.get("side") or "").upper()
        if not qty or action not in ("BUY", "SELL"):
            continue
        dollars = _positive(row.get("estimated_trade_dollars"))
        book["orders"].append(
            {
                "run_id": run_id,
                "submitted_at": submitted_at.isoformat(timespec="seconds"),
                "fills_at": execution.get("fills_at"),
                "auction": execution.get("summary"),
                "symbol": str(row["symbol"]).upper().strip(),
                "quantity": qty if action == "BUY" else -qty,
                "status": str(row.get("status") or ""),
                "order_id": _id(row.get("order_id")),
                "perm_id": _id(row.get("perm_id")),
                "price": dollars / qty if dollars else None,
            }
        )
    _atomic_write_json(path, book)
    return path


# ── reading IBKR and comparing ────────────────────────────────────────────


def _fills(client: Any) -> list[dict[str, Any]]:
    """The executions IBKR reports (the current day's), signed: sells negative."""
    out = []
    for fill in client.ib.fills():
        execution, contract = fill.execution, fill.contract
        qty = float(execution.shares)
        out.append(
            {
                "perm_id": _id(getattr(execution, "permId", None)),
                "order_id": _id(getattr(execution, "orderId", None)),
                "symbol": str(contract.symbol).upper().strip(),
                "quantity": -qty if str(execution.side).upper().startswith("S") else qty,
            }
        )
    return out


def read_account(profile: str, client: Any = None) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """IBKR's positions now (build_paper_basket.fetch_positions_df) and the fills it reports.

    ``client`` defaults to the read-only "reconcile" client; this connects and
    disconnects it. A failed connection or positions read raises
    BrokerUnavailableError. Fills that can't be read leave every order
    counted in full, which the report shows.
    """
    from src.broker.ibkr import reconciler
    from src.production.runtime.build_paper_basket import fetch_positions_df

    client = client or reconciler._make_readonly_client(profile)
    try:
        client.connect()
    except Exception as exc:  # noqa: BLE001
        raise reconciler.BrokerUnavailableError(
            f"IBKR connect failed at {client.config.host}:{client.config.port}: {exc}"
        ) from exc
    try:
        try:
            positions = fetch_positions_df(client)
        except Exception as exc:  # noqa: BLE001
            raise reconciler.BrokerUnavailableError(f"IBKR positions read failed: {exc}") from exc
        try:
            fills = _fills(client)
        except Exception as exc:  # noqa: BLE001
            log.warning("EOD reconcile: could not read IBKR's fills (%s); every order counts in full", exc)
            fills = []
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return positions, fills


def _fills_of(order: Mapping[str, Any], fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if order.get("perm_id"):
        return [f for f in fills if f["perm_id"] == order["perm_id"]]
    if order.get("order_id"):
        return [f for f in fills if f["order_id"] == order["order_id"] and f["symbol"] == order["symbol"]]
    return []


def expected_positions(
    book: Mapping[str, Any], fills: list[dict[str, Any]]
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """The book's positions plus each order at its fills, and how each order counted:
    at the fills IBKR reports for it, else in full (IBKR's API keeps one day of
    fills), or not at all when it never reached IBKR."""
    expected = dict(book["positions"])
    orders = []
    for order in book["orders"]:
        reported = _fills_of(order, fills)
        if order.get("status") in NOT_PLACED:
            filled, counted = 0.0, f"not counted: it never reached IBKR (status {order['status']})"
        elif reported:
            filled, counted = sum(f["quantity"] for f in reported), "at the fills IBKR reports"
        else:
            filled, counted = float(order["quantity"]), "in full: IBKR reports no fill for it (its API keeps the current day's)"
        expected[order["symbol"]] = expected.get(order["symbol"], 0.0) + filled
        orders.append({**order, "filled": filled, "counted": counted})
    return {s: q for s, q in expected.items() if abs(q) > SHARE_TOLERANCE}, orders


def _qty(x: float) -> float | int:
    return int(round(x)) if abs(x - round(x)) <= SHARE_TOLERANCE else round(x, 6)


def position_breaks(
    expected: Mapping[str, float],
    actual: Mapping[str, float],
    unit_values: Mapping[str, float],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Every symbol where IBKR's quantity differs from the expected one, valued
    at ``unit_values`` (dollars per unit; ``notional_diff_usd`` is None without one)."""
    breaks = []
    for symbol in sorted(set(expected) | set(actual)):
        want, have = expected.get(symbol, 0.0), actual.get(symbol, 0.0)
        if abs(have - want) <= SHARE_TOLERANCE:
            continue
        value = unit_values.get(symbol)
        breaks.append(
            {
                "symbol": symbol,
                "expected_qty": _qty(want),
                "broker_qty": _qty(have),
                "qty_diff": _qty(have - want),
                "ref_price": value,
                "notional_diff_usd": abs(have - want) * value if value else None,
                "as_of": as_of.isoformat() if as_of else None,
            }
        )
    return breaks


def _due(book: Mapping[str, Any]) -> datetime | None:
    """When IBKR's positions should show the last pending order's auction."""
    times = [datetime.fromisoformat(o["fills_at"]) for o in book["orders"] if o.get("fills_at")]
    return max(times) + SETTLE if times else None


# ── the reconcile ─────────────────────────────────────────────────────────


def reconcile_end_of_day(
    *,
    profile: str = "paper",
    material_threshold_usd: float = 100.0,
    as_of: date | None = None,
    arm_switch_on_mismatch: bool = True,
    root: Path | None = None,
    client: Any = None,
    now: datetime | None = None,
) -> EodReconcileResult:
    """Run the reconcile. Returns a structured result.

    ``root`` is the engine root holding ``data/broker/reconciliations`` (default
    this checkout); ``client`` an unconnected IBKR client (default the
    read-only "reconcile" one); ``now`` the time to check pending orders
    against (default now).

    Side effects:
      * writes the report under ``data/broker/reconciliations/`` (atomic
        write — survives mid-write crashes).
      * arms the kill switch if a break is material and
        ``arm_switch_on_mismatch=True``. Idempotent: if the switch is
        already armed we don't double-arm.
      * resets the expected book to IBKR's positions, once the report is
        written and the switch armed.

    Validates ``material_threshold_usd`` (must be finite >= 0) and
    ``profile`` (non-empty string).
    """
    if not isinstance(profile, str) or not profile:
        raise ValueError(f"profile must be a non-empty string, got {profile!r}")
    if not (isinstance(material_threshold_usd, (int, float))
            and math.isfinite(float(material_threshold_usd))
            and material_threshold_usd >= 0):
        raise ValueError(
            f"material_threshold_usd must be a finite non-negative number, "
            f"got {material_threshold_usd!r}"
        )

    as_of = as_of or date.today()
    if not isinstance(as_of, date):
        raise TypeError(f"as_of must be a datetime.date, got {type(as_of).__name__}")

    def result(ok: bool, **kw: Any) -> EodReconcileResult:
        kw.setdefault("diffs", [])
        kw.setdefault("material_mismatch", False)
        kw.setdefault("report_path", None)
        return EodReconcileResult(ok=ok, profile=profile, as_of=as_of.isoformat(), **kw)

    try:
        book = load_expected_book(profile, root)
    except ValueError as exc:
        log.error("EOD reconcile: %s", exc)
        return result(False, error=str(exc))
    if book is None:
        return result(True, skipped="no expected book yet: the first submission records one")

    from src.common import clock

    now = now or clock.now_et()
    due = _due(book)
    if due is not None and now < due:
        fills_at = (due - SETTLE).astimezone(clock.NYSE_TZ)
        return result(
            True,
            skipped=f"the last orders fill at {fills_at:%a %b %d %H:%M} ET; this reconciles them once "
                    f"IBKR's positions show it, from {due.astimezone(clock.NYSE_TZ):%H:%M} ET",
        )

    try:
        positions, fills = read_account(profile, client)
        actual, marks = _held(positions)
    except Exception as exc:  # noqa: BLE001 — an unreachable gateway is a failure, never "clean"
        log.error("EOD reconcile failed: %s", exc)
        return result(False, error=str(exc))

    expected, orders = expected_positions(book, fills)
    order_prices = {o["symbol"]: o["price"] for o in book["orders"] if o.get("price")}
    breaks = position_breaks(expected, actual, {**book["unit_values"], **order_prices, **marks}, as_of)
    material = any(
        b["notional_diff_usd"] is None or b["notional_diff_usd"] >= material_threshold_usd for b in breaks
    )

    report_path = _recon_dir(root) / f"{profile}_eod_{as_of.isoformat()}.json"
    payload = {
        "profile": profile,
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "material_threshold_usd": material_threshold_usd,
        "material_mismatch": material,
        "expected_from": {"positions_at": book["positions_at"], "positions_from": book["positions_from"]},
        "orders": orders,
        "expected": {s: _qty(q) for s, q in sorted(expected.items())},
        "actual": {s: _qty(q) for s, q in sorted(actual.items())},
        "diff_count": len(breaks),
        "diffs": breaks,
    }
    try:
        _atomic_write_json(report_path, payload)
    except OSError as exc:
        log.exception("EOD reconcile: failed to write report")
        return result(False, diffs=breaks, material_mismatch=material, orders=orders,
                      error=f"report write failed: {exc}")

    if material and arm_switch_on_mismatch:
        # Idempotent — don't re-arm if already armed (avoids overwriting
        # the original armed_at timestamp / reason on a re-run).
        try:
            already_armed = is_kill_switch_armed()
        except KillSwitchCorrupted:
            # Treat corruption as armed (per kill_switch contract).
            already_armed = True
        if not already_armed:
            log.error("Material mismatch detected — arming kill switch.")
            try:
                arm_kill_switch(
                    reason=f"EOD reconciliation mismatch on {as_of.isoformat()}; "
                           f"see {report_path.name}",
                    by="end_of_day_reconcile",
                )
            except Exception as exc:
                log.exception("EOD reconcile: failed to arm kill switch")
                return result(False, diffs=breaks, material_mismatch=material, orders=orders,
                              report_path=str(report_path), error=f"arm failed: {exc}")
        else:
            log.info("Material mismatch on rerun — kill switch already armed.")

    try:
        _atomic_write_json(
            book_path(profile, root),
            {
                "profile": profile,
                "positions_at": now.isoformat(timespec="seconds"),
                "positions_from": f"IBKR's positions at the reconcile of {as_of.isoformat()}",
                "positions": actual,
                "unit_values": marks,
                "orders": [],
            },
        )
    except OSError as exc:
        # The next reconcile would report these breaks again: say so now.
        log.exception("EOD reconcile: failed to reset the expected book")
        return result(False, diffs=breaks, material_mismatch=material, orders=orders,
                      report_path=str(report_path), error=f"expected book reset failed: {exc}")
    return result(True, diffs=breaks, material_mismatch=material, orders=orders, report_path=str(report_path))


__all__ = [
    "EodReconcileResult",
    "book_path",
    "expected_positions",
    "load_expected_book",
    "position_breaks",
    "read_account",
    "reconcile_end_of_day",
    "record_submission",
]
