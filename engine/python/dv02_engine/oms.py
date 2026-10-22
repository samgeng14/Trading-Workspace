"""The order manager's decisions (engine/src/oms.cpp): ports of the Python
runner's drift gate, basket and auction choice. The runner compares them with
its own on every run (trading-engine/src/production/engine_shadow.py); Python
still sends the orders.

    from dv02_engine import oms
    basket = oms.plan_basket([oms.Target("SPY", 0.6, 500.0)], [oms.Holding("SPY", 10, "STK", 501.0)],
                             nav=100_000, cash=40_000)
    [(o.side, o.quantity, o.symbol) for o in basket.orders]     # sells first, then buys
    oms.execution_for(["ARCA"], datetime(2026, 10, 21, 15, 35, tzinfo=nyse.NY))["order_type"]  # "MOC"
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from . import nyse
from ._core import oms as _core

Holding = _core.Holding
Target = _core.Target
Line = _core.Line
PlannedOrder = _core.PlannedOrder
Basket = _core.Basket
Weights = _core.Weights
DriftDecision = _core.DriftDecision

plan_basket = _core.plan_basket
current_weights = _core.current_weights
gate_targets = _core.gate_targets
moc_cutoff = _core.moc_cutoff
pairwise_sum = _core.pairwise_sum
clean_symbol = _core.clean_symbol

__all__ = [
    "Basket",
    "DriftDecision",
    "Holding",
    "Line",
    "PlannedOrder",
    "Target",
    "Weights",
    "clean_symbol",
    "current_weights",
    "execution_for",
    "gate_targets",
    "moc_cutoff",
    "pairwise_sum",
    "plan_basket",
    "rebalance_decision",
]


def rebalance_decision(
    target_weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    today: date,
    *,
    last_submitted_at: date | None = None,
    last_basket: Mapping[str, float] | None = None,
    drift_threshold: float = 0.05,
    composition_threshold: float = 0.05,
    cooldown_days: int = 3,
    force: bool = False,
    outside_model: Iterable[str] = (),
) -> DriftDecision:
    """The drift gate, as src/runtime/auto_rebalance.decide_rebalance_with_drift."""
    return _core.rebalance_decision(
        dict(target_weights),
        dict(current_weights),
        nyse.to_date(today),
        None if last_submitted_at is None else nyse.to_date(last_submitted_at),
        None if last_basket is None else dict(last_basket),
        drift_threshold,
        composition_threshold,
        cooldown_days,
        force,
        list(outside_model),
    )


def execution_for(exchanges: Iterable[str], at: datetime) -> dict[str, Any]:
    """How a batch whose symbols list on ``exchanges`` (their primary exchanges,
    one each) executes when sent at ``at``: order_type, tif, auction ("close" or
    "open"), session (date), fills_at and moc_deadline (New York times; the
    deadline is None for market-on-open)."""
    e = _core.execution_for(list(exchanges), nyse.to_instant(at))
    return {
        "order_type": e.order_type,
        "tif": e.tif,
        "auction": e.auction,
        "session": nyse.from_date(e.session),
        "fills_at": nyse.from_instant(e.fills_at),
        "moc_deadline": None if e.moc_deadline is None else nyse.from_instant(e.moc_deadline),
    }
