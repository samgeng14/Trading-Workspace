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

IBKR's adjusted closes also have bad days, which :func:`bad_days` finds and
:func:`signal_closes` repairs (see BLIP_MIN and CASH_LIKE below).
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

# IBKR's adjusted closes have bad days the price master doesn't. Checked against
# a vendor's total-return index (research/r5_bond_carry/data.py), there are two
# kinds:
#   * A blip: the factor (adjusted / master) steps and steps back within two
#     sessions. A dividend never reverses, so this is a close the two disagree
#     on for a day. TLT on 2023-08-09: 0.25% lower for one day.
#   * A missed distribution: a cash-like fund's price falls with no step in the
#     factor. BIL on 2024-12-19: a $0.39 payout (0.40%) that IBKR's series
#     doesn't have. From 2016 to 2026 BIL's price never fell more than 0.044% on
#     a day without one.
# The signals repair both. A blip's days take the factor of the day before it.
# A missed distribution is put back as an ex-date would be, so the fund's total
# return that day is 0 instead of minus the payout. Neither can be a real move:
# the repair only brings the signal back to the master's prices plus the fund's
# income.
BLIP_MIN = 0.0005  # a step in the factor of at least 5 bps ...
BLIP_CENTS = 0.025  # ... and at least 2.5 cents a share, so a cent's rounding isn't one
BLIP_BACK = 0.0002  # back within 2 bps (or a cent) of where it was ...
BLIP_SESSIONS = 2  # ... within two sessions
# T-bill funds, whose price falls only when they pay out. A missed payout can't
# be told from the price for funds whose price moves: SHY fell 0.51% on rate
# days with no payout, more than its payouts (0.02% to 0.35%).
CASH_LIKE = ("BIL",)
CASH_FALL = 0.001  # a one-day fall of 0.1%: twice BIL's largest without a payout
STEP_MIN = 0.0002  # a factor step this large is a payout IBKR recorded


@dataclass(frozen=True)
class BadDay:
    """A day IBKR's adjusted closes get wrong. ``size`` is the factor's step for a
    blip, the price's fall for a missed distribution; ``sessions`` is how many
    days it covers."""

    symbol: str
    date: pd.Timestamp
    kind: str  # "blip" or "missed_distribution"
    size: float
    sessions: int = 1

    def describe(self) -> str:
        day = self.date.date().isoformat()
        if self.kind == "blip":
            moves = "dips" if self.size < 0 else "jumps"
            span = "a session" if self.sessions == 1 else f"{self.sessions} sessions"
            return f"{self.symbol} {day}: IBKR's adjustment {moves} {abs(self.size):.2%} for {span}, then returns"
        return (f"{self.symbol} {day}: the price falls {abs(self.size):.2%} and IBKR's adjustment "
                "has no payout")  # fmt: skip


def bad_days(symbol: str, close: pd.Series, factor: pd.Series, since: pd.Timestamp | None = None) -> list[BadDay]:
    """The bad days in one symbol's adjusted closes. ``close`` is the master's
    closes in date order, ``factor`` adjusted/master on the same days (carried
    over the days the adjusted file doesn't have). ``since`` is the file's first
    day: up to it the factor is carried back, so a payout there is out of the
    file's reach rather than missed. After its last day a payout is missed until
    the next refresh, and is repaired as one."""
    c = close.to_numpy(dtype=float)
    f = factor.to_numpy(dtype=float)
    days = pd.DatetimeIndex(close.index)
    found: list[BadDay] = []
    i = 1
    while i < len(f):
        step = f[i] / f[i - 1] - 1
        if abs(step) >= max(BLIP_MIN, BLIP_CENTS / c[i]):
            back = next((k for k in range(1, BLIP_SESSIONS + 1)
                         if i + k < len(f) and abs(f[i + k] / f[i - 1] - 1) <= max(BLIP_BACK, 0.01 / c[i + k])),
                        None)  # fmt: skip
            if back is not None:
                found.append(BadDay(symbol, days[i], "blip", float(step), back))
                i += back
        i += 1
    if symbol in CASH_LIKE:
        fall = c[1:] / c[:-1] - 1
        stepped = f[1:] / f[:-1] - 1 >= STEP_MIN
        reach = days[:-1] >= since if since is not None else np.ones(len(fall), dtype=bool)  # both days in reach
        for j in np.flatnonzero((fall <= -CASH_FALL) & ~stepped & reach):
            found.append(BadDay(symbol, days[j + 1], "missed_distribution", float(fall[j])))
    return sorted(found, key=lambda b: b.date)


def repair(close: pd.Series, factor: pd.Series, found: list[BadDay]) -> pd.Series:
    """``factor`` with ``found`` put right: a blip's days take the factor of the
    day before it; a missed distribution scales every earlier day down by the
    price's fall, as IBKR scales history at an ex-date."""
    f = factor.to_numpy(dtype=float).copy()
    c = close.to_numpy(dtype=float)
    where = {d: i for i, d in enumerate(pd.DatetimeIndex(close.index))}
    for b in found:
        i = where[b.date]
        if b.kind == "blip":
            f[i : i + b.sessions] = f[i - 1]
    for b in found:
        if b.kind == "missed_distribution":
            i = where[b.date]
            f[:i] *= c[i] / c[i - 1]
    return pd.Series(f, index=factor.index)


@dataclass
class SignalPrices:
    basis: str  # "total_return" or "price"
    closes: pd.Series  # aligned to the prices frame's index
    on_price: dict[str, str] = field(default_factory=dict)  # symbol -> why it stays on price
    repaired: list[BadDay] = field(default_factory=list)  # IBKR's bad days, put right in ``closes``

    def note(self) -> str | None:
        """The symbols on price, grouped by why, and the days repaired, or None
        when there are none."""
        parts = []
        by_reason: dict[str, list[str]] = {}
        for symbol, why in sorted(self.on_price.items()):
            by_reason.setdefault(why, []).append(symbol)
        parts += [f"on price, {why}: {', '.join(syms)}" for why, syms in by_reason.items()]
        if self.repaired:
            latest = sorted(self.repaired, key=lambda b: b.date)[-3:]
            n = len(self.repaired)
            parts.append(f"{n} bad day{'s' if n != 1 else ''} in IBKR's adjusted closes repaired, the latest: "
                         + "; ".join(b.describe() for b in latest))  # fmt: skip
        return "; ".join(parts) or None


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


def factors(prices: pd.DataFrame, adjusted: pd.DataFrame | None) -> dict[str, tuple[pd.Series, pd.Series, pd.Series]]:
    """For each symbol in both, in date order: the master's closes, the factor
    adjusted/master on every master day (carried over the days the adjusted
    file doesn't have), and which days the adjusted file has, each indexed by
    the prices frame's rows."""
    adj = normalize_adjusted(adjusted)
    by_symbol = {s: g.set_index("date")["adj_close"] for s, g in adj.groupby("symbol")}
    out = {}
    for symbol, rows in prices.groupby("symbol"):
        series = by_symbol.get(symbol)
        if series is None:
            continue
        rows = rows.sort_values("date")
        shared = rows["date"].isin(series.index)
        factor = pd.Series(np.nan, index=rows.index)
        factor[shared.to_numpy()] = (series.reindex(rows.loc[shared, "date"]).to_numpy()
                                     / rows.loc[shared, "close"].to_numpy())  # fmt: skip
        # days without an adjusted close carry the nearest factor: price return since then
        out[symbol] = (rows["close"].astype(float), factor.ffill().bfill(), shared)
    return out


def dated(rows: pd.DataFrame, series: pd.Series) -> pd.Series:
    """``series`` (indexed by ``rows``' index) indexed by ``rows``' dates."""
    return pd.Series(series.to_numpy(), index=pd.DatetimeIndex(rows.loc[series.index, "date"]))


def _symbol_bad_days(prices: pd.DataFrame, symbol: str, close: pd.Series, factor: pd.Series,
                     shared: pd.Series) -> list[BadDay]:  # fmt: skip
    since = prices.loc[shared[shared].index, "date"].min()
    return bad_days(symbol, dated(prices, close), dated(prices, factor), since)


def find_bad_days(prices: pd.DataFrame, adjusted: pd.DataFrame | None) -> list[BadDay]:
    """Every bad day in ``adjusted`` against ``prices``, both as
    :func:`signal_closes` takes them, in date order."""
    found: list[BadDay] = []
    for symbol, (close, factor, shared) in factors(prices, adjusted).items():
        found += _symbol_bad_days(prices, symbol, close, factor, shared)
    return sorted(found, key=lambda b: (b.date, b.symbol))


def signal_closes(prices: pd.DataFrame, adjusted: pd.DataFrame | None) -> SignalPrices:
    """Total-return closes for ``prices`` (``date``, ``symbol``, ``close``).

    IBKR's bad days (:func:`bad_days`) are repaired first and listed in
    ``repaired``. A symbol with no adjusted rows, or whose adjusted rows then
    fail :func:`check_ratio`, keeps its price close, and ``on_price`` says why.
    With no adjusted file at all the whole frame is on price.
    """
    closes = prices["close"].astype(float).copy()
    if normalize_adjusted(adjusted).empty:
        return SignalPrices("price", closes, {s: "no adjusted closes yet" for s in prices["symbol"].unique()})

    on_price: dict[str, str] = {}
    repaired: list[BadDay] = []
    by_symbol = factors(prices, adjusted)
    for symbol in prices["symbol"].unique():
        if symbol not in by_symbol:
            on_price[symbol] = "no adjusted closes"
            continue
        close, factor, shared = by_symbol[symbol]
        found = _symbol_bad_days(prices, symbol, close, factor, shared)
        fixed = repair(dated(prices, close), dated(prices, factor), found)
        fixed.index = factor.index
        # a blip no longer looks like a split the two disagree on; a split still does
        why = check_ratio(pd.Series(fixed[shared].to_numpy(), index=prices.loc[shared[shared].index, "date"].to_numpy()))
        if why:
            on_price[symbol] = why
            continue
        repaired += found
        closes.loc[close.index] = close.to_numpy() * fixed.to_numpy()

    return SignalPrices("total_return", closes, on_price, sorted(repaired, key=lambda b: (b.date, b.symbol)))
