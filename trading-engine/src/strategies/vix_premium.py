"""
vix_premium.py — R3's rule, short the VIX futures premium, as researched
(trading-engine/research/r3_vix_futures: HYPOTHESIS.md, REPORT.md).

  * VXCM, the instrument: the 30-day constant-maturity VX roll, rebuilt from each
    monthly contract's daily settlement as S&P's VIX short-term futures index
    rolls (``cm30``'s er_return and er_level)
  * the signal: CM30, the two contracts around 30 calendar days interpolated,
    against the VIX close
  * at each close t: short VXCM at 10% of NAV while CM30 > VIX (contango), else
    flat; BIL holds the rest of the 99.5% budget. Decided daily, lag 1.

tests/unit/test_vix_premium.py keeps this identical to the research code, which
stays as it was run. Production reads only Cboe's current per-contract files
(contracts expiring from 2014), which need no rescaling.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

INSTRUMENT = "VXCM"
CASH = "BIL"
NOTIONAL = 0.10  # the short, a share of NAV
BUDGET = 0.995  # as S1's: every book's gross at each close; the rest is a cash buffer
CM_DAYS = 30
PRICE_COLUMNS = ["open", "high", "low", "close", "settle"]
CONTRACT_FILE = re.compile(r"VX_(\d{4}-\d{2}-\d{2})\.csv$")


# ── Cboe's files ─────────────────────────────────────────────────────────


def read_contract(path: Path) -> pd.DataFrame:
    """One contract's file: date, label, the prices in VIX points (NaN where there was
    none: both of Cboe's sources write 0), volume and open interest."""
    text = path.read_text(errors="replace")
    start = text.find("Trade Date")
    if start < 0:
        raise ValueError(f"{path.name}: no 'Trade Date' header")
    lines = text[start:].splitlines()
    width = lines[0].count(",")
    lines = [ln[:-1] if ln.count(",") == width + 1 and ln.endswith(",") else ln for ln in lines]
    raw = pd.read_csv(io.StringIO("\n".join(lines)), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["Trade Date"].str.strip(), format="mixed"),
        "label": raw["Futures"].str.strip(),
        **{c: pd.to_numeric(raw[c.title()], errors="coerce") for c in PRICE_COLUMNS},
        "volume": pd.to_numeric(raw["Total Volume"], errors="coerce"),
        "open_interest": pd.to_numeric(raw["Open Interest"], errors="coerce"),
    })  # fmt: skip
    out[PRICE_COLUMNS] = out[PRICE_COLUMNS].where(out[PRICE_COLUMNS] > 0)
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def contracts(paths: Iterable[Path]) -> pd.DataFrame:
    """One row per contract and trade date from Cboe's current files (VX_<expiry>.csv),
    with expiry and final (the expiry date's row, whose settle is the final settlement
    value, not a trading price)."""
    frames = []
    for path in sorted(paths):
        m = CONTRACT_FILE.search(path.name)
        if not m:
            continue
        frames.append(read_contract(path).assign(expiry=pd.Timestamp(m.group(1)), source="current", file=path.name))
    if not frames:
        raise ValueError("no VX contract files")
    out = pd.concat(frames, ignore_index=True)
    out = out[out["date"] <= out["expiry"]]
    out["final"] = out["date"] == out["expiry"]
    return out.sort_values(["expiry", "date"]).reset_index(drop=True)


def read_vix(path: Path) -> pd.Series:
    """VIX's daily close, by date (Cboe's VIX_History.csv)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().upper() for c in df.columns]
    s = pd.Series(pd.to_numeric(df["CLOSE"], errors="coerce").to_numpy(), index=pd.to_datetime(df["DATE"], format="mixed"))
    return s[~s.index.duplicated(keep="last")].sort_index().dropna().rename("vix")


def settles(rows: pd.DataFrame) -> pd.DataFrame:
    """Trade date x expiry: each contract's daily settlement on its regular trading days
    (the final settlement value left out)."""
    regular = rows[~rows["final"]]
    return regular.pivot(index="date", columns="expiry", values="settle").sort_index()


# ── the 30-day series ────────────────────────────────────────────────────


def cm30(table: pd.DataFrame) -> pd.DataFrame:
    """The 30-day series on every date where its contracts have settlements (``table``:
    settles()). Columns: front, second, cm30_price, w_front (the signal's contracts and
    the front's weight); first, second_roll, w_first (the S&P roll's contracts and the
    first's weight at the close); er_return (the day's return on the previous close's
    weights) and er_level (from 1.0)."""
    expiries = sorted(table.columns)
    dates = table.index

    rows = []
    for t in dates:
        later = [e for e in expiries if e > t]
        if len(later) < 2:
            continue
        f, s = later[0], later[1]
        p_f, p_s = table.at[t, f], table.at[t, s]
        d_f, d_s = (f - t).days, (s - t).days
        w = 1.0 if d_f >= CM_DAYS else (d_s - CM_DAYS) / (d_s - d_f)
        price = p_f if w == 1.0 else w * p_f + (1.0 - w) * p_s
        rows.append({"date": t, "front": f, "second": s, "cm30_price": price, "w_front": w})
    out = pd.DataFrame(rows).set_index("date")

    # the S&P roll: period k runs from the trading day before settlement S_k to the day
    # before the trading day before S_(k+1); in it the first month is S_(k+1), the second
    # S_(k+2), and the first's weight at the close of t is (trading days left) / dt
    starts = []
    for e in expiries:
        i = int(np.searchsorted(dates.values, np.datetime64(e), side="left")) - 1
        starts.append(i if i >= 0 else None)
    first, second, w1 = {}, {}, {}
    for k in range(len(expiries) - 2):
        if starts[k] is None or starts[k + 1] is None:
            continue
        a, b = starts[k], starts[k + 1]
        dt = b - a
        if dt <= 0:
            continue
        for i in range(a, b):
            t = dates[i]
            first[t], second[t] = expiries[k + 1], expiries[k + 2]
            w1[t] = (b - i) / dt
    roll = pd.DataFrame({"first": pd.Series(first), "second_roll": pd.Series(second), "w_first": pd.Series(w1)})
    out = out.join(roll, how="left")

    ret = {}
    prev = None
    for t in out.index:
        if prev is not None and pd.notna(out.at[prev, "first"]):
            c1, c2, w = out.at[prev, "first"], out.at[prev, "second_roll"], out.at[prev, "w_first"]
            p1a, p2a = table.at[prev, c1], table.at[prev, c2]
            p1b, p2b = table.at[t, c1], table.at[t, c2]
            base = w * p1a + (1.0 - w) * p2a
            ret[t] = (w * (p1b - p1a) + (1.0 - w) * (p2b - p2a)) / base
        prev = t
    out["er_return"] = pd.Series(ret)
    out["er_level"] = (1.0 + out["er_return"].fillna(0.0)).cumprod()
    return out


# ── the rule ─────────────────────────────────────────────────────────────


def latest(series: pd.Series, t: pd.Timestamp) -> float:
    """The latest value on or before t."""
    known = series.loc[:t]
    if known.empty:
        raise ValueError(f"no value by {t.date()}")
    return float(known.iloc[-1])


def contango(cm: pd.DataFrame, vix: pd.Series, t: pd.Timestamp) -> bool:
    """CM30 > VIX at the close of t."""
    return latest(cm["cm30_price"], t) > latest(vix, t)


def short_book(notional: float = NOTIONAL) -> dict[str, float]:
    return {INSTRUMENT: -notional, CASH: BUDGET - notional}


def rule(cm: pd.DataFrame, vix: pd.Series) -> Callable[[pd.DataFrame], dict[str, float]]:
    """The rule as a strategy for dv02_engine's walk-forward: short VXCM at 10% of NAV
    while in contango at the close of the history's last date, else flat in BIL."""

    def strategy(history: pd.DataFrame) -> dict[str, float]:
        return short_book() if contango(cm, vix, history.index[-1]) else {CASH: BUDGET}

    return strategy


def costs():
    """The research's costs: VXCM is a future (no borrow, moves no cash) paying 6 bps a
    side and 8 bps of notional a month for the roll; BIL takes S1's CostModel."""
    from dv02_engine.costs import CostModel, FuturesRoot, future

    return CostModel(
        half_spread_bps=2.0,
        half_spread_bps_by_symbol={INSTRUMENT: 6.0},
        impact_k=0.5,
        borrow_bps=25.0,
        borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
        cash_interest_bps=0.0,
        instruments={INSTRUMENT: future(1.0, INSTRUMENT)},
        futures={INSTRUMENT: FuturesRoot(commission=0.0, roll_bps=8.0, roll_months=list(range(1, 13)), roll_day=1)},
    )
