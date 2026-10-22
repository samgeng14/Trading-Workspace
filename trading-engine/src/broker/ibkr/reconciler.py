"""
reconciler.py — Diff IBKR account positions against the engine's book.

Used by ``runtime.end_of_day_reconcile``. Pulls the current positions
from IBKR and the most recent paper order blotter from disk, then
returns diffs as a list of dicts shaped like:

    {"symbol": str,
     "book_qty": int,
     "broker_qty": int,
     "qty_diff": int,
     "ref_price": float,
     "notional_diff_usd": float,
     "as_of": str | None}

Empty list = perfect match. The runtime EOD step decides which diffs
are 'material' based on ``notional_diff_usd``.

Failure handling: an IBKR connection or position-read failure raises
``BrokerUnavailableError``. It must not be turned into an empty list:
"no diffs" means the book matches the broker, and an unreachable
gateway proves nothing about that.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from .client import IBKRClient, IBKRClientError
from .settings import make_client

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BLOTTER_DIR = _PROJECT_ROOT / "data" / "broker" / "orders"


class BrokerUnavailableError(IBKRClientError):
    """IBKR could not be reached or did not return positions."""


def _read_paper_blotter_qty(profile: str) -> dict[str, int]:
    """Sum signed qty per symbol from the paper order blotter.

    Returns ``{symbol: int}`` or an empty dict if no blotter exists or
    cannot be parsed. Live profile is unsupported here — its blotter
    format may differ; we return an empty book and let the diff fall
    through to "broker positions are all surplus".
    """
    if profile != "paper":
        log.info("reconciler: blotter read skipped for profile=%s", profile)
        return {}

    parquet = _BLOTTER_DIR / "paper_order_blotter.parquet"
    csv = _BLOTTER_DIR / "paper_order_blotter.csv"
    path = parquet if parquet.exists() else (csv if csv.exists() else None)
    if path is None:
        log.info("reconciler: no paper blotter at %s or .csv", parquet)
        return {}

    try:
        import pandas as pd  # local import — pandas is heavy
    except ImportError:
        log.warning("reconciler: pandas not available; skipping blotter read")
        return {}

    try:
        df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("reconciler: blotter read failed (%s): %s", path, exc)
        return {}

    if df.empty or "symbol" not in df.columns:
        return {}

    qty_col = next(
        (c for c in ("delta_shares", "signed_qty", "qty", "quantity") if c in df.columns),
        None,
    )
    if qty_col is None:
        log.warning("reconciler: blotter missing qty column (%s)", list(df.columns))
        return {}

    side_col = next((c for c in ("side", "action") if c in df.columns), None)

    book: dict[str, int] = {}
    for _, row in df.iterrows():
        sym = str(row["symbol"]).strip().upper()
        if not sym:
            continue
        try:
            q = int(round(float(row[qty_col])))
        except (TypeError, ValueError):
            continue
        if side_col is not None:
            s = str(row.get(side_col, "")).strip().upper()
            if s in {"SELL", "SLD", "S"}:
                q = -abs(q)
            elif s in {"BUY", "BOT", "B"}:
                q = abs(q)
            # Unknown side → trust the sign already on q.
        book[sym] = book.get(sym, 0) + q
    return book


def _make_readonly_client(profile: str) -> IBKRClient:
    return make_client(profile, "reconcile", readonly=True)


def diff_book_vs_account(
    *,
    profile: str = "paper",
    as_of: date | None = None,
    client: IBKRClient | None = None,
    book: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Compute book-vs-account position diffs.

    Parameters
    ----------
    profile : "paper" or "live"
    as_of   : informational only; logged in each diff row.
    client  : optional injected ``IBKRClient`` (already connected). When
              omitted we open a short-lived read-only connection.
    book    : optional override of the engine's expected positions.
              When omitted we read from the on-disk paper blotter.
    """
    if book is None:
        book = _read_paper_blotter_qty(profile)

    owns_client = client is None
    if client is None:
        client = _make_readonly_client(profile)
        try:
            client.connect()
        except Exception as exc:  # noqa: BLE001
            raise BrokerUnavailableError(
                f"IBKR connect failed at {client.config.host}:{client.config.port}: {exc}"
            ) from exc

    try:
        try:
            broker_positions = client.positions(account=client.config.account or None)
        except Exception as exc:  # noqa: BLE001
            raise BrokerUnavailableError(f"IBKR positions read failed: {exc}") from exc
    finally:
        if owns_client:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    broker_qty: dict[str, int] = {}
    broker_price: dict[str, float] = {}
    for p in broker_positions:
        contract = getattr(p, "contract", None)
        sym = getattr(contract, "symbol", None) if contract is not None else None
        if not sym:
            continue
        sym = str(sym).strip().upper()
        try:
            q = int(round(float(getattr(p, "position", 0) or 0)))
        except (TypeError, ValueError):
            q = 0
        broker_qty[sym] = broker_qty.get(sym, 0) + q
        try:
            broker_price[sym] = float(getattr(p, "avgCost", 0.0) or 0.0)
        except (TypeError, ValueError):
            broker_price[sym] = 0.0

    all_syms = set(book) | set(broker_qty)
    diffs: list[dict[str, Any]] = []
    for sym in sorted(all_syms):
        b_qty = int(book.get(sym, 0))
        a_qty = int(broker_qty.get(sym, 0))
        if b_qty == a_qty:
            continue
        ref = float(broker_price.get(sym, 0.0))
        diffs.append({
            "symbol": sym,
            "book_qty": b_qty,
            "broker_qty": a_qty,
            "qty_diff": a_qty - b_qty,
            "ref_price": ref,
            "notional_diff_usd": float(abs(a_qty - b_qty)) * ref,
            "as_of": (as_of.isoformat() if as_of is not None else None),
        })
    return diffs


__all__ = ["BrokerUnavailableError", "diff_book_vs_account"]
