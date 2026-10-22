"""
data.py — R4's data and its checks: SPY's and QQQ's opens and closes from the ETF
master, their dividends, and the calendar. Phase 1 of the study looks at these
checks only, never at any return.

    python data.py        # print every check (nothing is downloaded)

Sources, all on disk:
  * the ETF master (trading-engine/data/market/cleaned/prices/etf_prices_master.parquet):
    open, high, low, close and volume. Its rows to 2026-02-09 are the vendor
    files' in data/market/raw/etf/; from 2026-02-10 they come from another
    source, most likely IBKR's daily bars (TRADES, useRTH=True,
    src/production/pipeline/append_ibkr_daily.py), though the vendor files
    run to 2026-03-06 (``check_sources``).
  * those vendor files' "Total Return (Gross)" column, a total-return index:
    the dividends, from 1993 for SPY and 1999 for QQQ (``vendor_dividends``).
  * IBKR's dividend-adjusted closes (etf_total_return_closes.parquet), from
    2016-10: a second reading of the same dividends (``ibkr_dividends``).
Nothing is downloaded. A second source for the opens was tried on 2026-10-22 and
refused: Stooq's CSV now sits behind a JavaScript check for browsers, and
Nasdaq's quote API drops clients that aren't browsers. Neither was worked
around, so the opens are checked against themselves (``check_opens``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[1]
sys.path.insert(0, str(ENGINE))
from src.common import clock  # noqa: E402

PRICES = ENGINE / "data" / "market" / "cleaned" / "prices"
VENDOR = ENGINE / "data" / "market" / "raw" / "etf"
SYMBOLS = ("SPY", "QQQ")
VENDOR_LAST = pd.Timestamp("2026-03-06")  # the vendor files' last row; IBKR's bars after it
FIRST_DECISION = pd.Timestamp("2008-01-02")  # the walk-forward's first decision
DIVIDEND_FLOOR = 0.02  # dollars: below this a total-return difference is the index's rounding


# ── reading ──────────────────────────────────────────────────────────────


def master(symbol: str) -> pd.DataFrame:
    m = pd.read_parquet(PRICES / "etf_prices_master.parquet")
    m = m[m["symbol"] == symbol].copy()
    m["date"] = pd.to_datetime(m["date"])
    return m.sort_values("date").set_index("date")[["open", "high", "low", "close", "volume"]]


def vendor(symbol: str) -> pd.DataFrame:
    """The vendor file: date, close (its "Price"), open, low, high, volume, and the total-return index."""
    raw = pd.read_csv(VENDOR / f"{symbol}.csv", encoding="utf-8-sig", dtype=str)
    num = lambda c: pd.to_numeric(raw[c].str.replace(",", ""), errors="coerce")  # noqa: E731
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["Date"], format="%m/%d/%y"),
        "close": num("Price"), "open": num("Open"), "low": num("Low"), "high": num("High"),
        "volume": num("CVol"), "tr": num("Total Return (Gross)"),
    })  # fmt: skip
    return out.sort_values("date").set_index("date")


def ibkr_adjusted(symbol: str) -> pd.Series:
    tr = pd.read_parquet(PRICES / "etf_total_return_closes.parquet")
    tr = tr[tr["symbol"] == symbol].copy()
    tr["date"] = pd.to_datetime(tr["date"])
    return tr.set_index("date")["adj_close"].sort_index()


# ── dividends ────────────────────────────────────────────────────────────


def vendor_differences(symbol: str) -> pd.Series:
    """Each day's cash the vendor's total-return index adds beyond the price move:
    TR_t / TR_t-1 = (P_t + D_t) / P_t-1, so D_t = TR_t / TR_t-1 * P_t-1 - P_t."""
    v = vendor(symbol)
    return v["tr"] / v["tr"].shift(1) * v["close"].shift(1) - v["close"]


def vendor_dividends(symbol: str) -> pd.Series:
    """Cash per share going ex on each date, from the vendor's total-return index."""
    d = vendor_differences(symbol)
    return d[d > DIVIDEND_FLOOR].round(4)


def ibkr_factor(symbol: str, closes: pd.Series) -> pd.Series:
    """IBKR's adjustment factor, adjusted close / close."""
    adj = ibkr_adjusted(symbol)
    return (adj / closes.reindex(adj.index)).dropna()


def ibkr_dividends(symbol: str, closes: pd.Series) -> pd.Series:
    """Cash per share going ex on each date, from IBKR's adjusted closes: the
    adjustment factor f = adj / close steps at an ex-date by 1 - D / P_t-1."""
    f = ibkr_factor(symbol, closes)
    d = closes.shift(1).reindex(f.index) * (1.0 - f.shift(1) / f)
    return d[d > DIVIDEND_FLOOR].round(4)


def dividends(symbol: str) -> pd.Series:
    """R4's dividends: the vendor's to its last row, IBKR's after."""
    closes = master(symbol)["close"]
    v = vendor_dividends(symbol)
    i = ibkr_dividends(symbol, closes)
    return pd.concat([v[v.index <= VENDOR_LAST], i[i.index > VENDOR_LAST]]).sort_index().rename("dividend")


# ── the checks ───────────────────────────────────────────────────────────


def check_opens(symbol: str) -> list[str]:
    m = master(symbol)
    prev_close = m["close"].shift(1)
    out = [f"{symbol}: {len(m)} sessions in the master, {m.index[0].date()} to {m.index[-1].date()}"]
    flat = (m["open"] == m["high"]) & (m["high"] == m["low"]) & (m["low"] == m["close"])
    out.append(f"  opens missing {int(m['open'].isna().sum())}, at or below 0 {int((m['open'] <= 0).sum())}, "
               f"outside the day's low-high {int(((m['open'] < m['low'] - 1e-9) | (m['open'] > m['high'] + 1e-9)).sum())}, "
               f"bars with one price all day {int(flat.sum())}")  # fmt: skip
    same_prev = m["open"] == prev_close
    same_close = m["open"] == m["close"]
    by_year = pd.DataFrame({"open = prior close": same_prev, "open = close": same_close}).groupby(m.index.year).sum()
    busy = by_year[(by_year > 0).any(axis=1)]
    out.append("  sessions where the open repeats a close, by year (a filled open looks like this):")
    out += [f"    {y}: {int(r['open = prior close'])} equal the prior close, {int(r['open = close'])} equal the day's close"
            for y, r in busy.iterrows()]  # fmt: skip
    gap = (m["open"] / prev_close - 1.0).abs()
    big = gap[gap > 0.07].sort_index()
    out.append(f"  sessions opening more than 7% from the prior close (checked against known events): {len(big)}: "
               + ", ".join(d.date().isoformat() for d in big.index))
    px = m.loc[m.index >= FIRST_DECISION, "close"]
    out.append(f"  closes from {FIRST_DECISION.date()}: lowest ${px.min():.2f} ({px.idxmin().date()}), highest "
               f"${px.max():.2f} ({px.idxmax().date()}); $0.005 a share is {50 / px.max():.2f} to {50 / px.min():.2f} bps")  # fmt: skip
    return out


def check_sources(symbol: str) -> list[str]:
    m, v = master(symbol), vendor(symbol)
    common = m.index.intersection(v.index)
    dc = (m.loc[common, "close"] - v.loc[common, "close"]).abs()
    do = (m.loc[common, "open"] - v.loc[common, "open"]).abs()
    out = [f"{symbol}: the master against the vendor file on {len(common)} common sessions "
           f"(to {common[-1].date()}): closes differ on {int((dc > 0.005).sum())}, opens on {int((do > 0.005).sum())}"]  # fmt: skip
    ratio = m.loc[common, "volume"] / v.loc[common, "volume"]
    switch = ratio.index[(ratio - 1.0).abs() > 1e-9][0]  # the first session whose volume isn't the vendor's
    late = common[common >= switch]
    out.append(f"  volumes equal the vendor's on every common session before {switch.date()}; on the {len(late)} from "
               f"it, a median {ratio.loc[late].median():.0%} of the vendor's: the master's rows there have another source")  # fmt: skip
    out.append(f"  opens differ before {switch.date()}: {int((do.loc[do.index < switch] > 0.005).sum())}; from it:")
    for col in ("open", "high", "low", "close"):
        gap = m.loc[late, col] - v.loc[late, col]
        hit = gap[gap.abs() > 0.005]
        bps = (hit.abs() / v.loc[hit.index, col] * 1e4).max() if len(hit) else 0.0
        out.append(f"    {col}s differ on {len(hit)} of {len(late)}, by at most ${hit.abs().max() if len(hit) else 0.0:.2f} "
                   f"({bps:.2f} bps); the master's higher on {int((hit > 0).sum())}, lower on {int((hit < 0).sum())}")  # fmt: skip
    after = m.loc[m.index > VENDOR_LAST]
    out.append(f"  rows after the vendor file (IBKR's daily bars): {len(after)}, {after.index[0].date()} to "
               f"{after.index[-1].date()}")  # fmt: skip
    return out


def check_dividends(symbol: str) -> list[str]:
    closes = master(symbol)["close"]
    v, i = vendor_dividends(symbol), ibkr_dividends(symbol, closes)
    f = ibkr_factor(symbol, closes)
    both = (v.index >= i.index[0]) & (v.index <= VENDOR_LAST)
    vv = v[both]
    ii = i[(i.index >= i.index[0]) & (i.index <= VENDOR_LAST)]
    matched = vv.index.intersection(ii.index)
    diff = (vv.loc[matched] - ii.loc[matched]).abs()
    out = [f"{symbol} dividends: the vendor's total-return index finds {len(v)} ex-dates, "
           f"{v.index[0].date()} to {v.index[-1].date()}; IBKR's adjusted closes (from {f.index[0].date()}) {len(i)}, "
           f"from {i.index[0].date()}"]  # fmt: skip
    out.append(f"  where both cover ({i.index[0].date()} to {VENDOR_LAST.date()}): vendor {len(vv)}, IBKR {len(ii)}, on the "
               f"same dates {len(matched)}; amounts differ by at most ${diff.max():.4f} (median ${diff.median():.4f})")  # fmt: skip
    only_v = vv.index.difference(ii.index)
    only_i = ii.index.difference(vv.index)
    if len(only_v) or len(only_i):
        out.append(f"  only the vendor's: {[d.date().isoformat() for d in only_v]}; only IBKR's: "
                   f"{[d.date().isoformat() for d in only_i]}")  # fmt: skip
    for d in only_i:
        k = f.index.get_loc(d)
        out.append(f"    IBKR's adjustment factor around {d.date()}: " + ", ".join(f"{f.iloc[j]:.6f}" for j in range(k - 1, k + 2)))
    raw = vendor_differences(symbol)
    out.append(f"  sessions where the vendor's index falls more than ${DIVIDEND_FLOOR} short of the price move (the reversal "
               f"a one-day error in it would leave): {int((raw < -DIVIDEND_FLOOR).sum())}")  # fmt: skip
    per_year = v.groupby(v.index.year).agg(["count", "sum"])
    odd = per_year[per_year["count"] != 4]
    out.append(f"  ex-dates a year: 4 in every year except {', '.join(f'{y} ({int(r['count'])})' for y, r in odd.iterrows()) or 'none'}")
    for y in per_year.index[per_year["count"] > 4]:
        x = v[v.index.year == y]
        out.append(f"    {y}: " + ", ".join(f"{t.date()} ${a:.4f}" + (" (IBKR's too)" if t in matched else "")
                                        for t, a in x.items()))  # fmt: skip
    out.append(f"  dollars a year, first and last full years: {per_year['sum'].iloc[1]:.2f} ({per_year.index[1]}), "
               f"{per_year['sum'].iloc[-2]:.2f} ({per_year.index[-2]})")  # fmt: skip
    late = i[i.index > VENDOR_LAST]
    out.append("  after the vendor file, IBKR's: " + ", ".join(f"{t.date()} ${a:.4f}" for t, a in late.items()))
    return out


def check_calendar(symbol: str = "SPY") -> list[str]:
    m = master(symbol)
    start, end = m.index[0].date(), m.index[-1].date()
    rules = pd.DatetimeIndex([d for d in pd.date_range(start, end) if clock.is_us_business_day(d.date())])
    missing = rules.difference(m.index)
    extra = m.index.difference(rules)
    early = [d for d in m.index if clock.is_early_close(d.date())]
    since = [d for d in early if d >= FIRST_DECISION]
    return [
        f"calendar ({symbol}): {len(m)} sessions in the master against {len(rules)} by clock.py's rules",
        f"  sessions the rules have and the master lacks: {[d.date().isoformat() for d in missing]}",
        f"  sessions the master has and the rules lack: {[d.date().isoformat() for d in extra]}",
        f"  early closes (13:00 ET) in the master: {len(early)}, {len(since)} of them from {FIRST_DECISION.date()}",
    ]


def checks() -> None:
    lines: list[str] = []
    for symbol in SYMBOLS:
        lines += check_opens(symbol) + check_sources(symbol) + check_dividends(symbol)
    lines += check_calendar("SPY")
    print("\n".join(lines))


if __name__ == "__main__":
    checks()
