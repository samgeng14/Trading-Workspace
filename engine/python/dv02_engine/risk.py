"""The fund's no-leverage mandate as a pre-trade check (engine/src/risk.cpp).

gross = (long + |short|) / NAV <= 100%, futures at notional; cash >= 0 after
the orders. Every holding counts, the model's or not. Anything unpriced blocks.
Orders that only shrink positions may go even while the book is over a limit.
"""

from __future__ import annotations

from typing import Any

from ._core import (
    AssetClass,
    Book,
    Breach,
    Exposure,
    Order,
    Policy,
    Position,
    Result,
    apply,
    asset_class_from_ibkr,
    asset_class_name,
    check,
    exposure,
    settles_in_cash,
)

__all__ = [
    "AssetClass",
    "Book",
    "Breach",
    "Exposure",
    "Order",
    "Policy",
    "Position",
    "Result",
    "apply",
    "asset_class_from_ibkr",
    "asset_class_name",
    "check",
    "exposure",
    "exposure_dict",
    "policy_dict",
    "result_dict",
    "settles_in_cash",
]


def exposure_dict(e: Exposure) -> dict[str, Any]:
    return {
        "nav": e.nav,
        "cash": e.cash,
        "long_value": e.long_value,
        "short_value": e.short_value,
        "gross": e.gross,
        "net": e.net,
        "cash_ratio": e.cash_ratio,
        "largest_name": e.largest_name or None,
        "largest_weight": e.largest_weight,
        "positions": e.positions,
    }


def policy_dict(p: Policy) -> dict[str, float]:
    return {
        "max_gross": p.max_gross,
        "min_cash": p.min_cash,
        "max_net": p.max_net,
        "min_net": p.min_net,
        "max_single_name": p.max_single_name,
    }


def result_dict(r: Result) -> dict[str, Any]:
    return {
        "ok": r.ok,
        "reduce_only": r.reduce_only,
        "before": exposure_dict(r.before),
        "after": exposure_dict(r.after),
        "breaches": [
            {"rule": b.rule, "blocking": b.blocking, "value": b.value, "limit": b.limit, "message": b.message}
            for b in r.breaches
        ],
        "buy_capacity": r.buy_capacity,
    }
