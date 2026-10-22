"""
data.py — R5's data and its checks: the Treasury yields, and TLT's, IEI's, SHY's and
BIL's prices with their coupons. Phase 1 of the study looks at these checks only:
no rule's return, and no bond return on any signal.

    python data.py        # fetch the yields not cached yet, then print every check

Sources:
  * FRED (St. Louis Fed), keyless CSVs: the Treasury's constant-maturity yields
    (H.15, the DGS series; DTB3 is the 3-month bill on a discount basis). Treasury's
    methodology page: the New York Fed collects indicative bid-side quotes "at or
    near 3:30 PM each trading day", and the curve is "usually available ... by 6:00
    PM Eastern Time each trading day". Cached in research/_data/fred/ (git-ignored).
  * Treasury's own daily par yield curve CSVs, a second source for the same yields
    (research/_data/treasury/, git-ignored): ``check_second_source``.
  * the ETF master (trading-engine/data/market/cleaned/prices/etf_prices_master.parquet):
    closes, highs, lows and volumes. Its rows to 2026-02-09 are the vendor files';
    after, most likely IBKR's daily bars (research/r4_overnight/data.py).
  * the vendor files (data/market/raw/etf/): a gross total-return index, to
    2026-03-06: the coupons (``tr_closes``).
  * IBKR's dividend-adjusted closes (etf_total_return_closes.parquet), from
    2016-10-18 (BIL), 2016-10-19 (TLT) and 2017-08-03 (IEI, SHY): a second reading
    of the coupons where both exist, and the only one after 2026-03-06.
Downloads are data only: parsed, never run.
"""
from __future__ import annotations

import math
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[1]  # trading-engine
DATA = HERE.parent / "_data"
PRICES = ENGINE / "data" / "market" / "cleaned" / "prices"
TARGETS = ENGINE / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet"
VENDOR = ENGINE / "data" / "market" / "raw" / "etf"

UA = "DV02 paper-fund research (github.com/samgeng14/Trading-Workspace)"
PAUSE_S = 1.0  # between requests: public servers, not ours
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
    "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)
SERIES = ("DGS1MO", "DGS3MO", "DTB3", "DGS1", "DGS2", "DGS3", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30")
TREASURY_COLUMNS = {
    "DGS1MO": "1 Mo", "DGS3MO": "3 Mo", "DGS1": "1 Yr", "DGS2": "2 Yr", "DGS3": "3 Yr",
    "DGS5": "5 Yr", "DGS7": "7 Yr", "DGS10": "10 Yr", "DGS20": "20 Yr", "DGS30": "30 Yr",
}  # fmt: skip
SECOND_SOURCE_YEARS = range(2005, 2027)

BONDS = ("TLT", "IEI", "SHY", "BIL")
START = pd.Timestamp("2005-01-03")  # S1's panel
FIRST_DECISION = pd.Timestamp("2008-01-02")
HOLDOUT_START = pd.Timestamp("2023-10-12")  # the last 756 sessions of the panel ending 2026-10-16
PANEL_END = pd.Timestamp("2026-10-16")
VENDOR_LAST = pd.Timestamp("2026-03-06")  # the vendor files' last row; IBKR's adjusted closes after it
DIVIDEND_FLOOR = 0.02  # dollars: below this a total-return difference is the index's rounding
OUTLIER_BPS = 5.0  # a daily total-return gap between the two sources worth explaining

# The issuers' product pages, read on 2026-10-22 (values as of 2026-10-20, the spreads
# for IEI and BIL as of 2026-10-21): average yield to maturity, effective (BIL:
# option-adjusted) duration, 30-day median bid/ask spread, expense ratio.
ISSUER = {
    "TLT": {"ytm": 5.48, "duration": 14.85, "spread_pct": 0.01, "expense_pct": 0.15, "wam": 26.04},
    "IEI": {"ytm": 4.95, "duration": 4.21, "spread_pct": 0.01, "expense_pct": 0.15, "wam": 4.72},
    "SHY": {"ytm": 4.76, "duration": 1.79, "spread_pct": 0.01, "expense_pct": 0.15, "wam": 1.90},
    "BIL": {"ytm": math.nan, "duration": 0.11, "spread_pct": 0.01, "expense_pct": 0.1353, "wam": 0.11},
}
ISSUER_DATE = pd.Timestamp("2026-10-20")

# R3's eight stress windows, the fund's crashes (R3's and R4's criteria use them).
STRESS = {
    "2008 crisis": ("2008-09-15", "2008-11-20"),
    "2010 flash crash": ("2010-04-23", "2010-07-02"),
    "2011": ("2011-07-22", "2011-10-03"),
    "2015-16": ("2015-08-17", "2016-02-11"),
    "February 2018": ("2018-01-26", "2018-02-09"),
    "late 2018": ("2018-09-20", "2018-12-24"),
    "2020 crash": ("2020-02-19", "2020-03-23"),
    "2022": ("2022-01-01", "2022-12-31"),
}
REVERSAL = 0.50  # points: a swing in the 10-year yield ends when it reverses this far
SELL_OFF = 1.00  # points: a rise in the 10-year at least this large is a bond sell-off window


# ── fetching ─────────────────────────────────────────────────────────────


def _get(url: str, path: Path, *, retries: int = 3) -> Path:
    """``url`` saved at ``path``, unless already there. A web page where data is
    expected (a maintenance page) is refused rather than cached."""
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
            if body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
                raise RuntimeError(f"{url} returned a web page, not data (maintenance?)")
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
            time.sleep(PAUSE_S)
            return path
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            last = exc
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError(f"couldn't fetch {url}: {last}")


def fred(series: str) -> pd.Series:
    """One FRED series, in percent, indexed by date; a blank (no quote that day) is NaN."""
    path = _get(FRED_CSV.format(series=series), DATA / "fred" / f"{series}.csv")
    raw = pd.read_csv(path, dtype=str)
    if list(raw.columns) != ["observation_date", series]:
        raise RuntimeError(f"{path.name}: unexpected columns {list(raw.columns)}")
    values = pd.to_numeric(raw[series].replace(".", ""), errors="coerce")
    return pd.Series(values.to_numpy(), index=pd.to_datetime(raw["observation_date"]), name=series)


def yields() -> pd.DataFrame:
    return pd.concat([fred(s) for s in SERIES], axis=1, sort=True)


def treasury(year: int) -> pd.DataFrame:
    """Treasury's par yield curve for one year, in percent, columns as FRED's names."""
    path = _get(TREASURY_CSV.format(year=year), DATA / "treasury" / f"par_yield_curve_{year}.csv")
    raw = pd.read_csv(path)
    out = pd.DataFrame(index=pd.to_datetime(raw["Date"], format="%m/%d/%Y"))
    for series, column in TREASURY_COLUMNS.items():
        if column in raw.columns:
            out[series] = pd.to_numeric(raw[column], errors="coerce").to_numpy()
    return out.sort_index()


THIRTY_BACK = pd.Timestamp("2006-02-09")  # Treasury's par curve has no 30-year from 2002-02-19 to 2006-02-08


def segment_yields(y: pd.DataFrame) -> pd.DataFrame:
    """Each ETF's yield and the bill's, in percent.
    TLT: the mean of the 20- and 30-year yields (its index holds bonds of 20 to 30
    years; weighted average maturity 26.04 years on 2026-10-20), the 20-year alone
    before THIRTY_BACK: FRED fills Treasury's gap in the 30-year with another series
    (``check_yields``). IEI: the 5-year (3 to 7 years, 4.72 on average). SHY: the
    2-year (1 to 3 years, 1.90). The bill: the 3-month, on the same bond-equivalent
    basis as the others."""
    tlt = y[["DGS20", "DGS30"]].mean(axis=1, skipna=False)
    early = y.index < THIRTY_BACK
    tlt[early] = y.loc[early, "DGS20"]
    return pd.DataFrame({"TLT": tlt, "IEI": y["DGS5"], "SHY": y["DGS2"], "bill": y["DGS3MO"]})


# ── the ETFs ─────────────────────────────────────────────────────────────


def master(symbol: str) -> pd.DataFrame:
    m = pd.read_parquet(PRICES / "etf_prices_master.parquet")
    m = m[m["symbol"] == symbol].copy()
    m["date"] = pd.to_datetime(m["date"])
    return m.sort_values("date").set_index("date")[["open", "high", "low", "close", "volume"]]


def vendor(symbol: str) -> pd.DataFrame:
    """The vendor file: close (its "Price"), high, low, volume and the total-return
    index. IEI's file has blank rows before its first close; they are dropped."""
    raw = pd.read_csv(VENDOR / f"{symbol}.csv", encoding="utf-8-sig", dtype=str)
    num = lambda c: pd.to_numeric(raw[c].str.replace(",", ""), errors="coerce")  # noqa: E731
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["Date"], format="%m/%d/%y"),
        "close": num("Price"), "high": num("High"), "low": num("Low"), "volume": num("CVol"),
        "tr": num("Total Return (Gross)"),
    })  # fmt: skip
    return out.dropna(subset=["close"]).sort_values("date").set_index("date")


def vendor_blank_rows(symbol: str) -> int:
    raw = pd.read_csv(VENDOR / f"{symbol}.csv", encoding="utf-8-sig", dtype=str)
    return int(raw["Price"].isna().sum())


def ibkr_adjusted(symbol: str) -> pd.Series:
    tr = pd.read_parquet(PRICES / "etf_total_return_closes.parquet")
    tr = tr[tr["symbol"] == symbol].copy()
    tr["date"] = pd.to_datetime(tr["date"])
    return tr.set_index("date")["adj_close"].sort_index()


def tr_closes(symbol: str) -> pd.Series:
    """The ETF's total-return closes: the vendor's index to its last row, IBKR's
    adjusted closes after, chained there, the level starting at the master's close
    on the first date from START (so the harness's per-share commission starts at
    the right size). The index's own daily factor carries the coupons: SHY's and
    BIL's monthly coupons in the years of zero rates were under a cent, below the
    index's rounding, so extracting them one by one (as R4 did for SPY) would drop
    them (``check_coupons``)."""
    closes = master(symbol)["close"]
    v = vendor(symbol)["tr"]
    a = ibkr_adjusted(symbol)
    dates = closes.loc[START:].index
    t0 = dates[0]
    level = v.reindex(dates[dates <= VENDOR_LAST]) / v.loc[t0] * closes.loc[t0]
    after = dates[dates > VENDOR_LAST]
    chained = a.reindex(after) / a.loc[VENDOR_LAST] * level.iloc[-1]
    out = pd.concat([level, chained]).rename(symbol)
    if out.isna().any():
        raise RuntimeError(f"{symbol}: total-return closes missing on {out.index[out.isna()][:5].tolist()}")
    return out


def coupons(symbol: str, source: str) -> pd.Series:
    """Cash per share going ex on each date (above DIVIDEND_FLOOR): from the vendor's
    index, D = TR_t / TR_t-1 * P_t-1 - P_t; from IBKR's, the adjustment factor
    f = adj / close steps at an ex-date by 1 - D / P_t-1."""
    closes = master(symbol)["close"]
    if source == "vendor":
        v = vendor(symbol)
        d = v["tr"] / v["tr"].shift(1) * v["close"].shift(1) - v["close"]
    else:
        a = ibkr_adjusted(symbol)
        f = (a / closes.reindex(a.index)).dropna()
        d = closes.shift(1).reindex(f.index) * (1.0 - f.shift(1) / f)
    return d[d > DIVIDEND_FLOOR].round(4)


def sessions() -> pd.DatetimeIndex:
    """The NYSE sessions in the master (TLT's rows: it has traded every session since 2002)."""
    return master("TLT").index


def month_ends(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Each month's last session."""
    s = pd.Series(dates, index=dates)
    return pd.DatetimeIndex(s.groupby([dates.year, dates.month]).max().to_numpy())


# ── the checks ───────────────────────────────────────────────────────────


def _dates(index) -> str:
    return ", ".join(d.date().isoformat() for d in index)


def check_yields(y: pd.DataFrame) -> list[str]:
    nyse = sessions()
    nyse = nyse[(nyse >= START) & (nyse <= PANEL_END)]
    out = ["yields (FRED, percent):"]
    for s in SERIES:
        x = y[s].dropna()
        live = y[s].loc[START:PANEL_END]
        blank = nyse[live.reindex(nyse).isna().to_numpy()]
        extra = live.dropna().index.difference(nyse)
        low = live[live <= 0]
        out.append(f"  {s}: {x.index[0].date()} to {x.index[-1].date()}; NYSE sessions from {START.date()} to "
                   f"{PANEL_END.date()} with no yield {len(blank)}, yields on days NYSE was shut {len(extra)}, "
                   f"values at or below 0: {len(low)}" + (f" ({low.min():.2f} lowest, {low.index[0].date()} to "
                                                         f"{low.index[-1].date()})" if len(low) else ""))  # fmt: skip
    blank = nyse[y["DGS20"].reindex(nyse).isna().to_numpy()]
    kinds = pd.Series([f"{d.month:02d}-{d.day:02d}" for d in blank]).value_counts()
    out.append(f"  the NYSE sessions with no 20-year yield ({len(blank)}), by month and day: "
               + ", ".join(f"{k} ({v})" for k, v in sorted(kinds.items())))
    others = [d for d in blank if (d.month, d.day) not in {(10, x) for x in range(8, 15)} | {(11, x) for x in range(10, 13)}]
    out.append(f"    outside Columbus Day (Oct 8-14) and Veterans Day (Nov 10-12): {_dates(pd.DatetimeIndex(others)) or 'none'}")
    extra = y["DGS20"].loc[START:PANEL_END].dropna().index.difference(nyse)
    out.append(f"  days with yields and no NYSE session: {_dates(extra)}")
    out.append("  the CMT yields bottom at 0.00 and are never negative; DTB3 (discount basis) reaches "
               f"{y['DTB3'].loc[START:PANEL_END].min():.2f}")  # fmt: skip
    t2006 = treasury(2006)
    filled = y["DGS30"].loc["2002-02-19":"2006-02-08"].notna().sum()
    before, after = y.loc["2006-02-08", ["DGS20", "DGS30"]], y.loc["2006-02-09", ["DGS20", "DGS30"]]
    out.append(f"  the 30-year: Treasury's par curve has none from 2002-02-19 to 2006-02-08 (its 2006 file starts the "
               f"30-year on {t2006['DGS30'].first_valid_index().date()}), but FRED's DGS30 has {filled} values there; "
               f"on 2006-02-08 FRED's 30-year was {before['DGS30']:.2f} with the 20-year at {before['DGS20']:.2f}, and on "
               f"2006-02-09 the par 30-year {after['DGS30']:.2f} with the 20-year at {after['DGS20']:.2f}. So TLT's yield "
               f"uses the 20-year alone before {THIRTY_BACK.date()}, two years before the first decision")  # fmt: skip
    seg = segment_yields(y)
    for name in ("bill", "TLT", "IEI", "SHY"):
        d = seg[name].loc[FIRST_DECISION:PANEL_END].dropna().diff().dropna()
        top = d.abs().sort_values(ascending=False).head(5)
        out.append(f"  largest daily changes of the {name} yield from {FIRST_DECISION.date()} (bps): "
                   + ", ".join(f"{t.date()} {d.loc[t] * 100:+.0f}" for t in top.index))
    both = y[["DGS3MO", "DTB3"]].loc[FIRST_DECISION:PANEL_END].dropna()
    diff = (both["DGS3MO"] - both["DTB3"]) * 100
    out.append(f"  3-month CMT less the 3-month bill on a discount basis (DTB3), from {FIRST_DECISION.date()}: median "
               f"{diff.median():+.1f} bps, 5th to 95th percentile {diff.quantile(0.05):+.1f} to {diff.quantile(0.95):+.1f}")  # fmt: skip
    spread = (y["DGS30"] - y["DGS20"]).loc[FIRST_DECISION:PANEL_END].dropna() * 100
    out.append(f"  30-year less 20-year, from {FIRST_DECISION.date()}: {spread.min():+.0f} to {spread.max():+.0f} bps "
               f"(median {spread.median():+.0f}); the roll-down it implies for TLT, about duration x the slope per "
               f"year of maturity, is at most {abs(spread).max() / 10 * 16 / 100:.2f} points a year at a duration of 16")  # fmt: skip
    return out


def check_second_source(y: pd.DataFrame) -> list[str]:
    out = ["second source, Treasury's par yield curve CSVs against FRED:"]
    worst, total, differ, missing = 0.0, 0, 0, []
    for year in SECOND_SOURCE_YEARS:
        try:
            t = treasury(year)
        except RuntimeError as exc:
            missing.append(f"{year} ({exc})")
            continue
        for s in t.columns:
            f = y[s].reindex(t.index)
            both = pd.DataFrame({"t": t[s], "f": f}).dropna()
            gap = (both["t"] - both["f"]).abs()
            total += len(both)
            differ += int((gap > 0.005).sum())
            worst = max(worst, float(gap.max()) if len(gap) else 0.0)
            only_t = t.index[(t[s].notna() & f.isna()).to_numpy()]
            only_f = t.index[(t[s].isna() & f.notna()).to_numpy()]
            if len(only_t) or len(only_f):
                show = lambda d: _dates(d) if len(d) <= 3 else f"{len(d)} days, {d[0].date()} to {d[-1].date()}"  # noqa: E731
                out.append(f"  {year} {s}: Treasury only: {show(only_t) or 'none'}; FRED only: {show(only_f) or 'none'}"
                           + (f" (FRED's values there: {', '.join(f'{x:.2f}' for x in f.loc[only_f])})" if 0 < len(only_f) <= 3 else ""))  # fmt: skip
    out.append(f"  {total} yields compared, {SECOND_SOURCE_YEARS[0]} to {SECOND_SOURCE_YEARS[-1]}: {differ} differ, "
               f"by at most {worst:.2f} points" + (f"; not fetched: {'; '.join(missing)}" if missing else ""))  # fmt: skip
    return out


def check_issuer_yields(y: pd.DataFrame) -> list[str]:
    seg = segment_yields(y)
    row = seg.loc[ISSUER_DATE]
    out = [f"the ETFs' yields to maturity (issuers' pages, {ISSUER_DATE.date()}) against the segment yields that day:"]
    for s in ("TLT", "IEI", "SHY"):
        out.append(f"  {s}: {ISSUER[s]['ytm']:.2f}% against {row[s]:.2f}%")
    out.append(f"  TLT's segment: the 20-year {y.loc[ISSUER_DATE, 'DGS20']:.2f}%, the 30-year {y.loc[ISSUER_DATE, 'DGS30']:.2f}%; "
               f"IEI's: the 3-, 5- and 7-year {y.loc[ISSUER_DATE, 'DGS3']:.2f}, {y.loc[ISSUER_DATE, 'DGS5']:.2f}, "
               f"{y.loc[ISSUER_DATE, 'DGS7']:.2f}%; SHY's: the 1-, 2- and 3-year {y.loc[ISSUER_DATE, 'DGS1']:.2f}, "
               f"{y.loc[ISSUER_DATE, 'DGS2']:.2f}, {y.loc[ISSUER_DATE, 'DGS3']:.2f}%; BIL's: the 1- and 3-month "
               f"{y.loc[ISSUER_DATE, 'DGS1MO']:.2f}, {y.loc[ISSUER_DATE, 'DGS3MO']:.2f}%")  # fmt: skip
    return out


def check_etfs() -> list[str]:
    out = []
    for s in BONDS:
        m, v, a = master(s), vendor(s), ibkr_adjusted(s)
        common = m.index.intersection(v.index)
        dc = (m.loc[common, "close"] - v.loc[common, "close"]).abs()
        out.append(f"{s}: master {m.index[0].date()} to {m.index[-1].date()} ({len(m)} sessions); vendor "
                   f"{v.index[0].date()} to {v.index[-1].date()}" + (f" (after {vendor_blank_rows(s)} blank rows)"
                   if vendor_blank_rows(s) else "") + f"; IBKR's adjusted closes {a.index[0].date()} to {a.index[-1].date()}")  # fmt: skip
        out.append(f"  the master's closes against the vendor's on {len(common)} common sessions: {int((dc > 0.005).sum())} differ; "
                   f"sessions missing from either: master {len(v.index.difference(m.index))}, vendor "
                   f"{len(m.index[m.index <= v.index[-1]].difference(v.index))}")  # fmt: skip
        gaps = m["close"].loc[m.index[0]:].isna().sum()
        out.append(f"  missing closes in the master: {int(gaps)}")
        rv, ra = v["tr"].pct_change(), a.pct_change()
        ov = rv.index.intersection(ra.index)
        ov = ov[(ov > a.index[0]) & (ov <= VENDOR_LAST)]
        d = (rv.loc[ov] - ra.loc[ov]) * 1e4
        years = (ov[-1] - ov[0]).days / 365.25
        growth = (1 + rv.loc[ov]).prod() / (1 + ra.loc[ov]).prod()
        quiet = d[d.abs() <= OUTLIER_BPS]
        out.append(f"  total returns, the vendor's index against IBKR's, {ov[0].date()} to {ov[-1].date()} ({len(ov)} "
                   f"sessions): daily gap sd {quiet.std():.2f} bps without the outliers below, the vendor's growth "
                   f"{(growth ** (1 / years) - 1) * 1e4:+.2f} bps a year above IBKR's")  # fmt: skip
        f = (a / m["close"].reindex(a.index)).dropna()
        for t, x in d[d.abs() > OUTLIER_BPS].items():
            k = f.index.get_loc(t)
            move = m["close"].loc[t] - m["close"].shift(1).loc[t]
            out.append(f"    {t.date()}: {x:+.1f} bps; IBKR's factor {f.iloc[k - 1]:.5f}, {f.iloc[k]:.5f}, "
                       f"{f.iloc[k + 1]:.5f}; the close moved ${move:+.2f}")  # fmt: skip
    return out


def check_coupons() -> list[str]:
    out = ["coupons (distributions above $0.02 a share), each year:"]
    for s in BONDS:
        v, i = coupons(s, "vendor"), coupons(s, "ibkr")
        per_year = v.groupby(v.index.year).size()
        years = range(max(v.index[0].year + 1, FIRST_DECISION.year), VENDOR_LAST.year)
        short = {y: int(per_year.get(y, 0)) for y in years if per_year.get(y, 0) != 12}
        out.append(f"  {s}: the vendor's index finds 12 every year from {years[0]} to {years[-1]} except "
                   + (", ".join(f"{y} ({n})" for y, n in short.items()) or "none"))
        both = (v.index >= i.index[0]) & (v.index <= VENDOR_LAST)
        ii = i[(i.index >= i.index[0]) & (i.index <= VENDOR_LAST)]
        matched = v[both].index.intersection(ii.index)
        diff = (v[both].loc[matched] - ii.loc[matched]).abs()
        out.append(f"    where both sources run ({i.index[0].date()} to {VENDOR_LAST.date()}): vendor {int(both.sum())}, "
                   f"IBKR {len(ii)}, on the same dates {len(matched)}, amounts within ${diff.max():.4f}; only the "
                   f"vendor's: {_dates(v[both].index.difference(ii.index)) or 'none'}; only IBKR's: "
                   f"{_dates(ii.index.difference(v[both].index)) or 'none'}")  # fmt: skip
    return out


def check_income(y: pd.DataFrame) -> list[str]:
    """Each year's income return (total-return growth over price growth), against the
    average yield of the ETF's segment: the coupons a fund pays lag the market's
    yields, since it holds bonds bought at older rates. Prices are left out: this is
    not a total return."""
    seg = segment_yields(y)
    out = ["income a year (total-return growth / price growth - 1) against the segment's average yield, percent:"]
    for s in BONDS:
        tr, px = tr_closes(s), master(s)["close"].loc[START:]
        rows = []
        for year in range(FIRST_DECISION.year, PANEL_END.year + 1):
            t, p = tr[tr.index.year == year], px[px.index.year == year]
            prev_t, prev_p = tr[tr.index.year == year - 1], px[px.index.year == year - 1]
            if not len(prev_t) or not len(t):
                continue
            income = (t.iloc[-1] / prev_t.iloc[-1]) / (p.iloc[-1] / prev_p.iloc[-1]) - 1
            name = "bill" if s == "BIL" else s
            avg = seg[name][seg.index.year == year].mean()
            rows.append(f"{year} {income * 100:.2f}/{avg:.2f}")
        out.append(f"  {s} (income/yield): " + ", ".join(rows))
    return out


def check_durations(y: pd.DataFrame) -> list[str]:
    """Each year's empirical duration: minus the slope of the ETF's daily total
    return on the daily change in its segment's yield (in decimal), with the R^2.
    The yields are 3:30 pm quotes and the closes 4 pm prints, so the slope is
    slightly attenuated."""
    seg = segment_yields(y)
    out = ["empirical duration by year (minus the slope of the daily total return on the yield change; R^2):"]
    for s in BONDS:
        name = "bill" if s == "BIL" else s
        r = tr_closes(s).pct_change()
        dy = seg[name].reindex(r.index).ffill().diff() / 100
        df = pd.DataFrame({"r": r, "dy": dy}).loc[FIRST_DECISION:PANEL_END].dropna()
        df = df[df["dy"] != 0]
        rows = []
        for year, g in df.groupby(df.index.year):
            c = np.cov(g["dy"], g["r"])
            slope = c[0, 1] / c[0, 0]
            r2 = c[0, 1] ** 2 / (c[0, 0] * c[1, 1])
            rows.append(f"{year} {-slope:.2f} ({r2:.2f})")
        out.append(f"  {s} on the {name} yield: " + ", ".join(rows))
        out.append(f"    the issuer's duration on {ISSUER_DATE.date()}: {ISSUER[s]['duration']:.2f}")
    return out


def decision_dates() -> pd.DatetimeIndex:
    """The walk-forward's first decision day, then each month's last session to the panel's end."""
    nyse = sessions()
    ends = month_ends(nyse[nyse <= PANEL_END])
    ends = ends[(ends > FIRST_DECISION) & (ends < PANEL_END)]
    return pd.DatetimeIndex([FIRST_DECISION]).append(ends)


def check_signal(y: pd.DataFrame) -> list[str]:
    """The carry each rule reads on its decision days, from the yields alone: the
    months it would be out of its bond. No return is computed."""
    seg = segment_yields(y).reindex(sessions()).ffill()
    days = decision_dates()
    out = ["the carry on each decision day (the segment's yield less the 3-month, points), from the yields only:"]
    for s in ("TLT", "IEI", "SHY"):
        carry = (seg[s] - seg["bill"]).reindex(days)
        wf, ho = carry[carry.index < HOLDOUT_START], carry[carry.index >= HOLDOUT_START]
        held = (carry > 0).astype(int)
        switches = int(held.diff().abs().sum())
        out.append(f"  {s}: walk-forward decisions ({wf.index[0].date()} to {wf.index[-1].date()}) {len(wf)}, carry at or "
                   f"below 0 on {int((wf <= 0).sum())} ({_months(wf.index[wf <= 0])}); holdout decisions "
                   f"({ho.index[0].date()} to {ho.index[-1].date()}) {len(ho)}, at or below 0 on {int((ho <= 0).sum())} "
                   f"({_months(ho.index[ho <= 0])}); switches in all {switches}")  # fmt: skip
        near = wf[wf.abs() < 0.25]
        out.append(f"    over the walk-forward's decisions: median {wf.median():.2f}, 10th percentile {wf.quantile(0.10):.2f}, "
                   f"lowest {wf.min():.2f} ({wf.idxmin().date()}), highest {wf.max():.2f} ({wf.idxmax().date()}); within "
                   f"0.25 of 0 on {len(near)}" + (f" ({_months(near.index)})" if len(near) else ""))  # fmt: skip
    return out


def _months(index: pd.DatetimeIndex) -> str:
    if not len(index):
        return "none"
    runs, start, prev = [], index[0], index[0]
    for t in index[1:]:
        if (t.year - prev.year) * 12 + t.month - prev.month != 1:
            runs.append((start, prev))
            start = t
        prev = t
    runs.append((start, prev))
    return ", ".join(f"{a:%Y-%m}" + (f" to {b:%Y-%m}" if b != a else "") for a, b in runs)


def sell_offs(ten: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Each rise in the 10-year yield of SELL_OFF points or more, from its closing low
    to its closing high, the swings split where the yield reverses REVERSAL points
    from its extreme (a zigzag on FRED's closes). First of tied closes."""
    ten = ten.dropna()
    swings, state = [], None
    lo = hi = ten.index[0]
    for t, v in ten.items():
        if v < ten[lo]:
            lo = t
        if v > ten[hi]:
            hi = t
        if state != "down" and ten[hi] - v >= REVERSAL and hi > lo:
            swings.append((lo, hi))
            state, lo = "down", t
        elif state != "up" and v - ten[lo] >= REVERSAL and lo > hi:
            state, hi = "up", t
    return [(a, b) for a, b in swings if ten[b] - ten[a] >= SELL_OFF - 1e-9]


def check_stress_windows(y: pd.DataFrame) -> list[str]:
    """The windows the crash criteria use, with the 10-year's move over each: yields only."""
    ten = y["DGS10"]
    rises = sell_offs(ten.loc["2007-06-01":PANEL_END])
    out = [f"bond sell-offs: every rise in the 10-year of {SELL_OFF:.2f} points or more, low to high, between "
           f"{REVERSAL:.2f}-point reversals, from FRED's closes:"]  # fmt: skip
    for a, b in rises:
        where = "walk-forward" if b < HOLDOUT_START else ("holdout" if a >= HOLDOUT_START else "straddles the holdout's start")
        out.append(f"  {a.date()} {ten[a]:.2f} to {b.date()} {ten[b]:.2f}: +{(ten[b] - ten[a]) * 100:.0f} bps, "
                   f"{(b - a).days} days ({where})")  # fmt: skip
    out.append("R3's eight stress windows, the 10-year's change over each:")
    for name, (a, b) in STRESS.items():
        x = ten.loc[:b].dropna()
        start = ten.loc[:a].dropna()
        out.append(f"  {name} ({a} to {b}): {start.iloc[-1]:.2f} to {x.iloc[-1]:.2f}, "
                   f"{(x.iloc[-1] - start.iloc[-1]) * 100:+.0f} bps")  # fmt: skip
    return out


def check_costs() -> list[str]:
    out = ["costs:"]
    for s in BONDS:
        m = master(s).loc[FIRST_DECISION:PANEL_END]
        px = m["close"]
        adv = (m["close"] * m["volume"]).groupby(m.index.year).median() / 1e6
        out.append(f"  {s}: close from {FIRST_DECISION.date()} ${px.min():.2f} ({px.idxmin().date()}) to ${px.max():.2f} "
                   f"({px.idxmax().date()}): $0.005 a share is {50 / px.max():.2f} to {50 / px.min():.2f} bps, and one "
                   f"cent of spread {100 / px.max():.2f} to {100 / px.min():.2f} bps; the issuer's 30-day median spread "
                   f"{ISSUER[s]['spread_pct']:.2f}%")  # fmt: skip
        out.append("    median daily dollar volume ($M) by year: " + ", ".join(f"{y} {v:,.0f}" for y, v in adv.items()))
        if s in ("BIL", "SHY"):
            rng = ((m["high"] - m["low"]) * 100).groupby(m.index.year).median()
            out.append("    median daily high-low range (cents), an upper bound on the quoted spread: "
                       + ", ".join(f"{y} {v:.0f}" for y, v in rng.items()))
    return out


def check_s0_basis() -> list[str]:
    """Every ETF S0 holds: the vendor's total-return index against IBKR's adjusted
    closes where both run, and the income a year the price-only basis leaves out
    before IBKR's series starts."""
    symbols = sorted(pd.read_parquet(TARGETS)["symbol"].unique())
    out = ["S0's ETFs, the vendor's total-return index against IBKR's adjusted closes:"]
    for s in symbols:
        m, v, a = master(s), vendor(s), ibkr_adjusted(s)
        rv, ra = v["tr"].pct_change(), a.pct_change()
        ov = rv.index.intersection(ra.index)
        ov = ov[(ov > a.index[0]) & (ov <= VENDOR_LAST)]
        if len(ov) < 2:
            out.append(f"  {s}: no overlap")
            continue
        d = (rv.loc[ov] - ra.loc[ov]) * 1e4
        years = (ov[-1] - ov[0]).days / 365.25
        gap = ((1 + rv.loc[ov]).prod() / (1 + ra.loc[ov]).prod()) ** (1 / years) - 1
        pre = v.loc[: a.index[0]]
        span = (pre.index[-1] - pre.index[0]).days / 365.25 if len(pre) > 1 else 0.0
        income = ((pre["tr"].iloc[-1] / pre["tr"].iloc[0]) / (pre["close"].iloc[-1] / pre["close"].iloc[0])) ** (1 / span) - 1 if span > 1 else math.nan
        common = m.index.intersection(v.index)
        ratio = (m.loc[common, "close"] / v.loc[common, "close"]).median()
        big = d[d.abs() > OUTLIER_BPS]
        out.append(f"  {s}: from {ov[0].date()}, gap {gap * 1e4:+.2f} bps a year, daily sd {d[d.abs() <= OUTLIER_BPS].std():.2f} bps"
                   + (f", outliers {', '.join(f'{t.date()} {x:+.1f}' for t, x in big.items())}" if len(big) else "")
                   + (f"; income left out before {a.index[0].date()}: {income * 100:.2f}% a year" if not math.isnan(income) else "")
                   + (f"; the master's closes are {ratio:.2f}x the vendor's" if abs(ratio - 1) > 1e-6 else ""))  # fmt: skip
    return out


def checks() -> None:
    y = yields()
    lines = check_yields(y) + check_second_source(y) + check_issuer_yields(y)
    lines += check_etfs() + check_coupons() + check_income(y) + check_durations(y)
    lines += check_signal(y) + check_stress_windows(y) + check_costs() + check_s0_basis()
    print("\n".join(lines))


if __name__ == "__main__":
    checks()
