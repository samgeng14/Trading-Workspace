"""
trend.py — S1's cross-asset trend rule on ETF proxies, as researched
(trading-engine/research/s1_trend, HYPOTHESIS.md; the long/flat version is the
trend half of H2, research/s3a_vol_overlay).

On each decision date, from closes up to that date only:
  * an asset trades once it has 253 closes;
  * its signal is the mean of the signs of its 63, 126 and 252-day returns, with
    negative signs set to 0 when long/flat;
  * its weight is signal over its 63-day volatility, scaled so every eligible
    asset at full signal would fill 99.5% of NAV, capped at 20% a name, and
    scaled down (never up) to a 10% a year ex-ante volatility;
  * BIL holds the rest.

tests/unit/test_trend.py keeps this identical to the research code, which stays
as it was run.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

RISKY = ["SPY", "VEA", "VWO", "TLT", "IEI", "GLD", "SLV", "USO", "UNG", "VNQ"]
CASH = "BIL"
LOOKBACKS = (63, 126, 252)
HISTORY_ROWS = 253  # closes an asset needs up to t to be traded
VOL_ROWS = 63
BUDGET = 0.995  # gross with every eligible asset at full signal; the rest is a cash buffer
NAME_CAP = 0.20
VOL_CEILING = 0.10
TRADING_DAYS = 252
REBALANCE_EVERY = 21
START = "2005-01-03"
# UNG's closes miss 28 days from 2010-06-03 to 2010-08-04: its history counts from after them
FIRST_HISTORY = {"UNG": pd.Timestamp("2010-08-05")}


def costs():
    """The research's cost model: IBKR Pro commissions, 2 bps half-spread,
    square-root impact, borrow on shorts."""
    from dv02_engine.costs import CostModel

    return CostModel(
        half_spread_bps=2.0,
        impact_k=0.5,
        borrow_bps=25.0,
        borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
        cash_interest_bps=0.0,  # idle money sits in BIL, which earns the T-bill rate
    )


def price_panel(prices_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes, volumes), one column per symbol from START: raw closes up to each
    fund's first dividend-adjusted close, IBKR's adjusted closes after it, chained
    there. Gaps inside a fund's history carry its last close."""
    master = pd.read_parquet(prices_dir / "etf_prices_master.parquet")
    master["date"] = pd.to_datetime(master["date"])
    raw = master.pivot(index="date", columns="symbol", values="close").sort_index()
    volumes = master.pivot(index="date", columns="symbol", values="volume").sort_index()
    tr = pd.read_parquet(prices_dir / "etf_total_return_closes.parquet")
    tr["date"] = pd.to_datetime(tr["date"])
    adjusted = tr.pivot(index="date", columns="symbol", values="adj_close").sort_index()

    closes = {}
    for symbol in raw.columns:
        x = raw[symbol]
        first = x.first_valid_index()
        x = pd.concat([x.loc[: first - pd.Timedelta(days=1)], x.loc[first:].ffill()])
        a = adjusted[symbol].dropna() if symbol in adjusted.columns else pd.Series(dtype=float)
        a = a[a.index.isin(x.dropna().index)]
        chained = x.copy()
        if len(a):
            t0 = a.index[0]
            after = x.index >= t0
            chained[after] = x.loc[t0] * (a.reindex(x.index[after]).ffill() / a.loc[t0]).to_numpy()
        closes[symbol] = chained
    panel = pd.DataFrame(closes).loc[START:]
    return panel, volumes.reindex(index=panel.index, columns=panel.columns)


def trend_rule(lookbacks: tuple[int, ...] = LOOKBACKS, *, long_flat: bool = True) -> Callable[[pd.DataFrame], dict | None]:
    """The rule as a strategy for dv02_engine's walk-forward: ``history`` is every
    close up to the decision date; the result is the target weights, or None
    when there isn't enough history to decide."""

    def strategy(history: pd.DataFrame) -> dict | None:
        tail = history.iloc[-HISTORY_ROWS:]
        if len(tail) < HISTORY_ROWS:
            return None
        eligible = [
            a for a in RISKY
            if a in tail.columns and tail[a].notna().all() and tail.index[0] >= FIRST_HISTORY.get(a, tail.index[0])
        ]  # fmt: skip
        cash_ok = bool(np.isfinite(history[CASH].iloc[-1]))
        if not eligible:
            return {CASH: BUDGET} if cash_ok else None
        last = tail.iloc[-1]
        signal = {}
        for a in eligible:
            signs = [float(np.sign(last[a] / tail[a].iloc[-1 - lb] - 1.0)) for lb in lookbacks]
            if long_flat:
                signs = [max(s, 0.0) for s in signs]
            signal[a] = float(np.mean(signs))
        returns = tail[eligible].iloc[-(VOL_ROWS + 1) :].pct_change(fill_method=None).iloc[1:]
        sigma = returns.std(ddof=1) * math.sqrt(TRADING_DAYS)
        sigma = sigma[sigma > 0]
        if sigma.empty:
            return {CASH: BUDGET} if cash_ok else None
        inverse = 1.0 / sigma
        w = pd.Series({a: signal[a] * inverse[a] for a in sigma.index}) * (BUDGET / inverse.sum())
        w = w.clip(-NAME_CAP, NAME_CAP)
        cov = returns[sigma.index].cov().to_numpy() * TRADING_DAYS
        book_vol = math.sqrt(max(float(w.to_numpy() @ cov @ w.to_numpy()), 0.0))
        if book_vol > VOL_CEILING:
            w = w * (VOL_CEILING / book_vol)
        weights = {a: float(v) for a, v in w.items() if v != 0.0}
        if cash_ok:
            weights[CASH] = BUDGET - float(w.abs().sum())
        return weights

    return strategy
