"""Total-return closes for the model's signals: the price master plus dividends.

The price master is IBKR TRADES bars, adjusted for splits but not dividends,
which is right for sizing orders and wrong for signals: a bond ETF or a T-bill
fund pays its return as income, so its price momentum misses most of it (BIL's
price barely moves). IBKR's ADJUSTED_LAST bars are adjusted for splits and
dividends, as of the day they are fetched, and are kept in a file of their own
(src/production/pipeline/append_total_return.py).

:func:`signal_closes` gives each master row a total-return close. On a day the
adjusted file has, it is IBKR's adjusted close. Days after the file's last
(it is refreshed after each run) continue at the master's price return, so a
dividend paid since then counts from the next refresh; days before its first
continue the same way backwards. The ratio adjusted/master is also the check
that the two agree on splits. Dividends only ever scale history down, so the
ratio never falls from one day to the next; a symbol whose ratio does, or
that goes above 1, is on a different split basis, and it stays on price.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# A day-to-day fall in adjusted/master larger than this is not rounding (two
# closes rounded to the cent move it by well under 0.5%), so the two series
# disagree on a split or a bad bar.
MAX_RATIO_FALL = 0.01
# The ratio is 1 on the fetch day and below it before; above this the series
# can't be a dividend adjustment.
MAX_RATIO = 1.02


@dataclass
class SignalPrices:
    basis: str  # "total_return" or "price"
    closes: pd.Series  # aligned to the prices frame's index
    on_price: dict[str, str] = field(default_factory=dict)  # symbol -> why it stays on price

    def note(self) -> str | None:
        """The symbols on price, grouped by why, or None when there are none."""
        if not self.on_price:
            return None
        by_reason: dict[str, list[str]] = {}
        for symbol, why in sorted(self.on_price.items()):
            by_reason.setdefault(why, []).append(symbol)
        return "; ".join(f"on price, {why}: {', '.join(syms)}" for why, syms in by_reason.items())


def normalize_adjusted(df: pd.DataFrame | None) -> pd.DataFrame:
    """``date``, ``symbol``, ``adj_close`` from an adjusted-closes frame, cleaned."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "symbol", "adj_close"])
    out = df.rename(columns={c: c.lower().strip() for c in df.columns})
    if "adj_close" not in out.columns and "close" in out.columns:
        out = out.rename(columns={"close": "adj_close"})
    out = out[["date", "symbol", "adj_close"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out["adj_close"] = pd.to_numeric(out["adj_close"], errors="coerce")
    out = out[np.isfinite(out["adj_close"]) & (out["adj_close"] > 0)]
    return out.drop_duplicates(["symbol", "date"], keep="last").sort_values(["symbol", "date"]).reset_index(drop=True)


def check_ratio(ratio: pd.Series) -> str | None:
    """Why ``ratio`` (adjusted/master on shared days, in date order) can't be a
    dividend adjustment, or None when it can."""
    if ratio.empty:
        return "no day in common with the price master"
    if not np.isfinite(ratio).all() or (ratio <= 0).any():
        return "a non-positive or missing ratio to the price master"
    if ratio.max() > MAX_RATIO:
        return f"adjusted close {ratio.max():.3f}x the master's: a different split basis"
    falls = ratio / ratio.shift(1) - 1
    worst = falls.min()
    if pd.notna(worst) and worst < -MAX_RATIO_FALL:
        day = falls.idxmin()
        return f"adjusted/master falls {-worst:.1%} on {pd.Timestamp(day).date()}: a split the two disagree on"
    return None


def signal_closes(prices: pd.DataFrame, adjusted: pd.DataFrame | None) -> SignalPrices:
    """Total-return closes for ``prices`` (``date``, ``symbol``, ``close``).

    A symbol with no adjusted rows, or whose adjusted rows fail
    :func:`check_ratio`, keeps its price close, and ``on_price`` says why.
    With no adjusted file at all the whole frame is on price.
    """
    closes = prices["close"].astype(float).copy()
    adj = normalize_adjusted(adjusted)
    if adj.empty:
        return SignalPrices("price", closes, {s: "no adjusted closes yet" for s in prices["symbol"].unique()})

    on_price: dict[str, str] = {}
    by_symbol = {s: g.set_index("date")["adj_close"] for s, g in adj.groupby("symbol")}
    for symbol, rows in prices.groupby("symbol"):
        series = by_symbol.get(symbol)
        if series is None:
            on_price[symbol] = "no adjusted closes"
            continue
        day = rows["date"]
        shared = day.isin(series.index)
        ratio = series.reindex(day[shared]).to_numpy() / rows.loc[shared, "close"].to_numpy()
        why = check_ratio(pd.Series(ratio, index=day[shared].to_numpy()))
        if why:
            on_price[symbol] = why
            continue
        factor = pd.Series(np.nan, index=rows.index)
        factor[shared.to_numpy()] = ratio
        # days without an adjusted close carry the nearest factor: price return since then
        factor = factor.ffill().bfill()
        closes.loc[rows.index] = rows["close"].to_numpy() * factor.to_numpy()

    return SignalPrices("total_return", closes, on_price)
