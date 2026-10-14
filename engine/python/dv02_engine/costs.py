"""The costs the live book pays (engine/src/costs.cpp), for backtests now and the live engine later.

    from dv02_engine.costs import CostModel, FuturesRoot, future
    costs = CostModel(
        half_spread_bps=2.0, half_spread_bps_by_symbol={"SPY": 0.5},
        impact_k=0.5, borrow_bps=25.0, cash_interest_bps=400.0,
        instruments={"MES": future(5)},                        # a symbol not listed is an equity
        # a root the table lacks, with your own all-in fee; added to the defaults
        futures={"CL": FuturesRoot(commission=2.50, roll_months=list(range(1, 13)), roll_day=15)},
    )
    HawkBacktester(1_000_000, costs=costs).run(prices, weights, volumes)

What each part charges (dollars, out of cash):

- commission: IBKR Pro Fixed for stocks and ETFs ($0.005 a share, $1.00 minimum,
  1% cap, per asset per rebalance); for futures, contracts x the root's all-in
  per-contract fee (default_futures() has ES, NQ, RTY, MES, MNQ, M2K, ZT, ZF, ZN,
  TN, ZB; any other root must be added).
- spread: half_spread_bps of the dollars traded (per-symbol overrides).
- impact: impact_k x daily vol x sqrt(dollars traded / ADV), as a fraction of the
  dollars traded. Daily vol and ADV come from the impact_lookback price dates
  before the trade date (never the trade date). Without volumes, or with too
  little history, a trade pays no impact and is counted in
  unmeasured_impact_trades.
- borrow: borrow_bps a year on short stock and ETF market value, actual/360,
  for the calendar days since the last price date.
- interest: cash_interest_bps a year earned on positive cash (excluding short
  proceeds; futures notional counts as cash), actual/360. 0 is off; the default
  is IBKR Pro's USD rate, its benchmark less 0.5% (338 bps in October 2026),
  which moves with the Fed: set the rate of the period backtested.
- roll: on each roll date of a held future, roll_bps of its notional plus
  both legs' commission.

The functions below price one trade or one stretch of carry, the same ones
the backtester calls.
"""

from __future__ import annotations

from ._core import costs as _core

CostModel = _core.CostModel
FuturesRoot = _core.FuturesRoot
Instrument = _core.Instrument
Kind = _core.Kind

default_futures = _core.default_futures
ibkr_pro_fixed = _core.ibkr_pro_fixed
futures_commission = _core.futures_commission
spread_cost = _core.spread_cost
impact_bps = _core.impact_bps
impact_cost = _core.impact_cost
borrow_cost = _core.borrow_cost
cash_interest = _core.cash_interest
roll_cost = _core.roll_cost
daily_volatility = _core.daily_volatility
average_dollar_volume = _core.average_dollar_volume

__all__ = [
    "CostModel",
    "FuturesRoot",
    "Instrument",
    "Kind",
    "average_dollar_volume",
    "borrow_cost",
    "cash_interest",
    "daily_volatility",
    "default_futures",
    "future",
    "futures_commission",
    "ibkr_pro_fixed",
    "impact_bps",
    "impact_cost",
    "roll_cost",
    "spread_cost",
]


def future(multiplier: float, root: str | None = None) -> Instrument:
    """A futures instrument: dollars per point, and its root in the futures table (default: the symbol)."""
    return Instrument(Kind.FUTURE, root or "", float(multiplier))
