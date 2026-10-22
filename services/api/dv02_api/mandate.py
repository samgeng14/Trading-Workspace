"""The fund's no-leverage mandate, checked by the C++ engine over the whole account.

gross = (long + |short|) / NAV <= 100%, futures at notional, leveraged and
inverse ETFs at their economic exposure (TQQQ at 3x), and cash >= 0 after the
orders: no margin loan. Every holding counts, the model's or not. The rules
live in engine/src/risk.cpp (``dv02_engine.risk``); this module only turns an
AccountSnap into the engine's book. The exposure table is the daily runner's
own (``EXPOSURE`` in trading-engine/src/runtime/leverage.py), so both order
paths count a geared ETF the same way.
"""

from __future__ import annotations

import math
import sys
from typing import Any, Iterable

from .broker import AccountSnap, PositionSnap

# dv02_engine.risk.Policy's defaults, readable without the engine installed
POLICY = {"max_gross": 1.0, "min_cash": 0.0, "max_net": 1.0, "min_net": -1.0, "max_single_name": 0.25}


class EngineMissing(RuntimeError):
    """dv02_engine can't be imported, so leverage can't be measured and every order is blocked."""


def engine() -> Any:
    try:
        from dv02_engine import risk
    except ImportError:
        raise EngineMissing(
            f"the C++ engine (dv02_engine) is not installed for {sys.executable}, so leverage can't be "
            "checked and orders are blocked. Run bin/tradeit setup"
        ) from None
    return risk


def price_of(p: PositionSnap) -> float:
    """Last price, else what IBKR's market value implies, else 0: the engine blocks that as unpriced."""
    if p.last is not None and math.isfinite(p.last) and p.last > 0:
        return p.last
    unit = p.quantity * p.multiplier
    implied = p.market_value / unit if unit else math.nan
    return implied if math.isfinite(implied) and implied > 0 else 0.0


def exposure_of(symbol: str, sec_type: str = "STK") -> float:
    """Exposure per dollar held (TQQQ 3.0): the engine's table, for shares only."""
    # imported here: it brings pandas, which the API otherwise loads only when it needs it
    from src.runtime.leverage import exposure_of as table

    return table(symbol, sec_type)


def book_from_snapshot(snap: AccountSnap) -> Any:
    """Every position in the account, priced, with NAV and cash."""
    risk = engine()
    positions = [
        risk.Position(
            p.symbol,
            p.quantity,
            price_of(p),
            p.multiplier,
            risk.asset_class_from_ibkr(p.sec_type),
            exposure_of(p.symbol, p.sec_type),
        )
        for p in snap.positions
    ]
    return risk.Book(snap.nav, snap.cash, positions)


def exposure(snap: AccountSnap) -> dict[str, Any]:
    """The book as it stands: gross, net and cash as fractions of NAV. ``unmeasured`` says
    what keeps the engine from measuring it (no NAV, a holding it can't price, options);
    gross leaves those holdings out, and the engine blocks every order until they're gone."""
    risk = engine()
    res = risk.result_dict(risk.check(book_from_snapshot(snap), [], risk.Policy(**POLICY)))
    inputs = ("nav", "unpriced", "options")
    return {**res["before"], "unmeasured": [b["message"] for b in res["breaches"] if b["rule"] in inputs]}


def check(snap: AccountSnap, orders: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The book after ``orders`` against POLICY, as ``risk.result_dict``.

    Each order has symbol, quantity (signed), price and commission, and optionally
    sec_type (default ETF, which is all the model trades). Its exposure comes from
    the engine's table, as the book's do: a TQQQ order counts at 3x.
    """
    risk = engine()
    planned = [
        risk.Order(
            o["symbol"],
            o["quantity"],
            o["price"],
            1.0,
            risk.asset_class_from_ibkr(o.get("sec_type", "ETF")),
            o.get("commission", 0.0),
            exposure_of(o["symbol"], o.get("sec_type", "ETF")),
        )
        for o in orders
    ]
    return risk.result_dict(risk.check(book_from_snapshot(snap), planned, risk.Policy(**POLICY)))
