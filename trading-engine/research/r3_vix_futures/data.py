"""
data.py — R3's free data, downloaded once into research/_data/cfe/ (git-ignored) and read
from there after: the Cboe Futures Exchange's daily history for every monthly VX (VIX
futures) contract since VX began trading on 2004-03-26, and VIX itself (R2's cached Cboe
file, research/_data/cboe/VIX_History.csv, fetched here too if it is missing).

    python data.py            # fetch what isn't cached yet, then print the data checks

Two Cboe sources, both public and keyless:
  * the archive: one CSV per contract, for contracts that expired from 2004 to 2014,
    cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<month code><yy>_VX.csv
  * the current files: one CSV per contract from 2013 on, listed by
    www.cboe.com/us/futures/market_statistics/historical_data/product/list/VX/
    and served from cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/.
    The 2013 files there carry no settlements (all 0), so 2013's contracts come from
    the archive.
Weekly VX contracts (from 2015) are left out: the 30-day series needs the monthlies only.

Before 2007-03-26 VX was quoted at ten times the VIX (a $100 multiplier); Cboe rescaled it
that day to the VIX itself (a $1,000 multiplier). Archive prices before then are divided by
10 here, so every price is in VIX points.

cm30() builds the 30-day constant-maturity series from the monthly settlements:
  * cm30_price, the basis signal's 30-day futures price: the two contracts around 30
    calendar days, interpolated linearly in days to expiry (the front alone when it is
    30 days or more away);
  * er_return and er_level, what holding that maturity earns: the S&P VIX Short-Term
    Futures Index's daily roll from the first month to the second, from the trading day
    before one monthly settlement to the trading day before the next, with each day's
    return on the previous close's weights (an excess-return index, no collateral).
Nothing here trades or measures a strategy. Downloads are data only: parsed, never run.
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "_data"
CFE_DIR = DATA / "cfe"
ARCHIVE_DIR = CFE_DIR / "archive"
CURRENT_DIR = CFE_DIR / "current"
LIST_PATH = CFE_DIR / "vx_list.json"
VIX_PATH = DATA / "cboe" / "VIX_History.csv"
USER_AGENT = "DV02 paper-fund research (github.com/samgeng14/Trading-Workspace; samcygeng.314@gmail.com)"
PAUSE_S = 1.0  # between files: Cboe's servers are public, not ours

ARCHIVE_URL = "https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_{code}{yy}_VX.csv"
LIST_URL = "https://www.cboe.com/us/futures/market_statistics/historical_data/product/list/VX/"
CURRENT_URL = "https://cdn.cboe.com/{path}"
VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
MONTH_CODES = "FGHJKMNQUVXZ"  # January to December
ARCHIVE_YEARS = range(2004, 2015)
FROM_CURRENT = date(2014, 1, 1)  # contracts expiring from here on come from the current files
RESCALED = pd.Timestamp("2007-03-26")  # VX quoted at 10x the VIX before this trade date
PRICE_COLUMNS = ["open", "high", "low", "close", "settle"]
CM_DAYS = 30


# ── downloads ────────────────────────────────────────────────────────────


def _get(url: str) -> bytes | None:
    """The body, or None when the file isn't there (403 or 404: Cboe answers 403 for a
    contract that was never listed)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
            time.sleep(PAUSE_S)
            return body
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                time.sleep(PAUSE_S)
                return None
            if attempt == 2:
                raise
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
        time.sleep(5 * (attempt + 1))
    return None


def _save(path: Path, body: bytes) -> None:
    if body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        raise RuntimeError(f"{path.name}: got a web page, not a CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def fetch_archive(refresh: bool = False) -> list[str]:
    """Every archive file for a month code and year from 2004 to 2014; returns the names
    found. A contract never listed has no file, and a marker remembers that."""
    found = []
    for year in ARCHIVE_YEARS:
        for code in MONTH_CODES:
            name = f"CFE_{code}{year % 100:02d}_VX.csv"
            path, missing = ARCHIVE_DIR / name, ARCHIVE_DIR / f"{name}.missing"
            if not refresh and path.exists():
                found.append(name)
                continue
            if not refresh and missing.exists():
                continue
            body = _get(ARCHIVE_URL.format(code=code, yy=f"{year % 100:02d}"))
            if body is None:
                missing.parent.mkdir(parents=True, exist_ok=True)
                missing.write_text("not on Cboe's archive\n")
                continue
            _save(path, body)
            found.append(name)
    return found


def fetch_current(refresh: bool = False) -> list[dict]:
    """Cboe's list of VX contracts, then each monthly contract's file. A contract that has
    expired is fetched once; one still trading again whenever the list is refreshed."""
    if refresh or not LIST_PATH.exists():
        body = _get(LIST_URL)
        if body is None:
            raise RuntimeError(f"Cboe's VX contract list isn't at {LIST_URL}")
        _save(LIST_PATH, body)
    listing = json.loads(LIST_PATH.read_text())
    today = date.today()
    monthly = [e for year in sorted(listing) for e in listing[year] if e.get("duration_type") == "M"]
    for e in monthly:
        expiry = date.fromisoformat(e["expire_date"])
        path = CURRENT_DIR / Path(e["path"]).name
        if path.exists() and not (refresh and expiry >= today):
            continue
        body = _get(CURRENT_URL.format(path=e["path"]))
        if body is not None:
            _save(path, body)
    return monthly


def fetch_vix(refresh: bool = False) -> None:
    if refresh or not VIX_PATH.exists():
        body = _get(VIX_URL)
        if body is None:
            raise RuntimeError("Cboe's VIX history isn't there")
        _save(VIX_PATH, body)


def fetch(refresh: bool = False) -> None:
    fetch_vix(refresh)
    fetch_archive(refresh)
    fetch_current(refresh)


# ── reading ──────────────────────────────────────────────────────────────


def _read(path: Path) -> pd.DataFrame:
    text = path.read_text(errors="replace")
    start = text.find("Trade Date")  # the archive's files from mid-2013 open with a disclaimer line
    if start < 0:
        raise ValueError(f"{path.name}: no 'Trade Date' header")
    lines = text[start:].splitlines()
    width = lines[0].count(",")
    # a few 2004-05 archive rows end with an extra empty field
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
    # 0 is how both sources write "no price"
    out[PRICE_COLUMNS] = out[PRICE_COLUMNS].where(out[PRICE_COLUMNS] > 0)
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def contracts() -> pd.DataFrame:
    """One row per contract and trade date: date, expiry (the final settlement date),
    source, the prices in VIX points (NaN where there was none), volume and open interest.
    final is True on the expiry date, whose settle is the final settlement value (the
    special opening quotation), not a trading price."""
    frames = []
    for path in sorted(ARCHIVE_DIR.glob("CFE_*_VX.csv")):
        df = _read(path)
        if df.empty:
            continue
        expiry = df["date"].iloc[-1]  # an archive file ends on its final settlement date
        if expiry.date() >= FROM_CURRENT:
            continue
        before = df["date"] < RESCALED
        df.loc[before, PRICE_COLUMNS] = df.loc[before, PRICE_COLUMNS] / 10.0
        frames.append(df.assign(expiry=expiry, source="archive", file=path.name))
    for path in sorted(CURRENT_DIR.glob("VX_*.csv")):
        expiry = pd.Timestamp(re.search(r"VX_(\d{4}-\d{2}-\d{2})\.csv", path.name).group(1))
        if expiry.date() < FROM_CURRENT:
            continue
        frames.append(_read(path).assign(expiry=expiry, source="current", file=path.name))
    out = pd.concat(frames, ignore_index=True)
    out = out[out["date"] <= out["expiry"]]
    out["final"] = out["date"] == out["expiry"]
    return out.sort_values(["expiry", "date"]).reset_index(drop=True)


def vix() -> pd.Series:
    """VIX's daily close, by date."""
    df = pd.read_csv(VIX_PATH)
    df.columns = [c.strip().upper() for c in df.columns]
    s = pd.Series(pd.to_numeric(df["CLOSE"], errors="coerce").to_numpy(), index=pd.to_datetime(df["DATE"], format="mixed"))
    return s[~s.index.duplicated(keep="last")].sort_index().dropna().rename("vix")


def settles(rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Trade date x expiry: each contract's daily settlement on its regular trading days
    (the final settlement value left out)."""
    rows = contracts() if rows is None else rows
    regular = rows[~rows["final"]]
    return regular.pivot(index="date", columns="expiry", values="settle").sort_index()


# ── the 30-day constant-maturity series ──────────────────────────────────


def cm30(table: pd.DataFrame | None = None) -> pd.DataFrame:
    """The 30-day series on every date where its contracts have settlements.

    Columns: front, second (expiries, calendar-day sense), cm30_price; first, second_roll,
    w_first (the S&P roll's contracts and the first's weight at the close); er_return
    (the day's return on the previous close's weights) and er_level (from 1.0).
    """
    table = settles() if table is None else table
    expiries = sorted(table.columns)
    dates = table.index

    rows = []
    for t in dates:
        # the basis signal: the contracts around 30 calendar days, interpolated
        later = [e for e in expiries if e > t]
        if len(later) < 2:
            continue
        f, s = later[0], later[1]
        p_f, p_s = table.at[t, f], table.at[t, s]
        d_f, d_s = (f - t).days, (s - t).days
        if d_f >= CM_DAYS:
            w = 1.0
        else:
            w = (d_s - CM_DAYS) / (d_s - d_f)
        price = p_f if w == 1.0 else w * p_f + (1.0 - w) * p_s
        rows.append({"date": t, "front": f, "second": s, "cm30_price": price, "w_front": w})
    out = pd.DataFrame(rows).set_index("date")

    # the S&P roll: period k runs from the trading day before settlement S_k to the day before
    # the trading day before S_(k+1); in it the first month is S_(k+1), the second S_(k+2),
    # and the first's weight at the close of t is (trading days from t to the period's end) / dt
    starts = []
    for e in expiries:
        i = int(np.searchsorted(dates.values, np.datetime64(e), side="left")) - 1  # the trading day before e
        starts.append(i if i >= 0 else None)
    first, second, w1 = {}, {}, {}
    for k in range(len(expiries) - 2):
        if starts[k] is None or starts[k + 1] is None:
            continue
        a, b = starts[k], starts[k + 1]  # [a, b) in trading-day positions
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


# ── the data checks ──────────────────────────────────────────────────────


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return date.fromordinal(d.toordinal() + (4 - d.weekday()) % 7)


def rule_expiry(year: int, month: int) -> date:
    """VX's settlement date by rule: the Wednesday 30 days before the third Friday of the
    calendar month after the contract month (Cboe moves it a day earlier when that
    Friday or the Wednesday is an exchange holiday)."""
    nxt = (year + month // 12, month % 12 + 1)
    return date.fromordinal(third_friday(*nxt).toordinal() - 30)


def checks() -> None:
    rows = contracts()
    table = settles(rows)
    v = vix()
    print(f"VX contracts: {rows['expiry'].nunique()} monthly, expiring {rows['expiry'].min().date()} to "
          f"{rows['expiry'].max().date()}; {len(rows):,} contract-days, {rows['settle'].notna().sum():,} with a settle")  # fmt: skip
    per_year = rows.groupby(rows["expiry"].dt.year)["expiry"].nunique()
    print("  contracts per expiry year:", ", ".join(f"{y}: {n}" for y, n in per_year.items()))
    src = rows.groupby("source")["expiry"].nunique()
    print("  by source:", dict(src))

    # 2013's contracts are in both sources; the current files' settles there are 0
    both = []
    for path in sorted(CURRENT_DIR.glob("VX_2013-*.csv")):
        cur = _read(path).set_index("date")
        exp = pd.Timestamp(re.search(r"VX_(\d{4}-\d{2}-\d{2})", path.name).group(1))
        arc = rows[(rows["expiry"] == exp) & (rows["source"] == "archive")].set_index("date")
        common = cur.index.intersection(arc.index)
        both.append((exp.date(), int(cur["settle"].notna().sum()), int(arc["settle"].notna().sum()),
                     float((cur.loc[common, "close"] - arc.loc[common, "close"]).abs().max())))  # fmt: skip
    if both:
        print("  2013 contracts in both sources (expiry, settles in current, in archive, max |close diff|):")
        print("   ", "; ".join(f"{e} {a}/{b} {d:.4f}" for e, a, b, d in both))

    # the rescale: the front contract against VIX either side of 2007-03-26
    around = table.loc["2007-03-19":"2007-03-30"]
    fronts = [(t, next(e for e in table.columns if e > t)) for t in around.index]
    print("  rescale (front settle / VIX, after dividing pre-2007-03-26 prices by 10):",
          ", ".join(f"{t.date()} {table.at[t, e] / v.get(t, np.nan):.3f}" for t, e in fronts))  # fmt: skip

    # expiry dates against the rule (the contract month is the expiry's own month)
    off = []
    for exp in sorted(pd.Timestamp(e) for e in rows["expiry"].unique()):
        rule = rule_expiry(exp.year, exp.month)
        if exp.date() not in (rule, date.fromordinal(rule.toordinal() - 1)):
            off.append(f"{exp.date()} (rule {rule})")
    wd = pd.Series([pd.Timestamp(e).dayofweek for e in rows["expiry"].unique()]).value_counts().sort_index().to_dict()
    print(f"  expiries by weekday (0 Mon): {wd}; off the rule's Wednesday (or the day before): {off or 'none'}")

    # settle against close on days the front traded
    rows_f = rows[(~rows["final"]) & (rows["volume"] > 0) & rows["close"].notna() & rows["settle"].notna()]
    rel = ((rows_f["settle"] - rows_f["close"]).abs() / rows_f["settle"])
    print(f"  |settle - close| / settle on traded days: median {rel.median():.4%}, 99th pct {rel.quantile(0.99):.2%}, "
          f"max {rel.max():.1%}")  # fmt: skip

    cm = cm30(table)
    print(f"30-day series: {cm.index.min().date()} to {cm.index.max().date()}, {len(cm):,} days; "
          f"S&P roll weights from {cm['first'].first_valid_index().date()}")  # fmt: skip
    gaps = cm[cm["er_return"].isna()].index
    print(f"  days with no roll return (no previous close, or a contract without a settle): {len(gaps)}, by year "
          f"{pd.Series(gaps.year).value_counts().sort_index().to_dict()}")  # fmt: skip
    # settlement errors: a move of more than 25% that reverses by more than 25% the next day
    r = np.log(table).diff()
    spikes = []
    for e in table.columns:
        x = r[e].dropna()
        for i in range(1, len(x) - 1):
            if abs(x.iloc[i]) > 0.25 and abs(x.iloc[i + 1]) > 0.25 and np.sign(x.iloc[i]) != np.sign(x.iloc[i + 1]):
                t = x.index[i]
                used = t in cm.index and e in (cm.at[t, "front"], cm.at[t, "second"], cm.at[t, "first"], cm.at[t, "second_roll"])
                spikes.append(f"{t.date()} in {pd.Timestamp(e).date()} ({x.iloc[i]:+.2f}, {x.iloc[i + 1]:+.2f} log; "
                              f"{'used by the 30-day series' if used else 'not used'})")  # fmt: skip
    print(f"  settle spikes that reverse the next day: {len(spikes)}", *("\n    " + sp for sp in spikes))
    one = cm.loc["2018-01-12":"2018-02-16", ["first", "second_roll", "w_first"]]
    print("  one roll period (the trading day before each settlement starts one):",
          ", ".join(f"{t:%m-%d} {f:%b} {w:.2f}" for t, f, w in zip(one.index, one["first"], one["w_first"])))  # fmt: skip
    spacing = (cm["second"] - cm["front"]).dt.days
    print(f"  front-to-second spacing, calendar days: min {spacing.min()}, median {spacing.median():.0f}, "
          f"max {spacing.max()}; dates with spacing over 40 days: {int((spacing > 40).sum())}")  # fmt: skip
    if (spacing > 40).any():
        wide = cm[spacing > 40]
        print("   ", f"{wide.index.min().date()} to {wide.index.max().date()}")
    w = cm["w_front"]
    print(f"  basis interpolation: front alone (30+ days out) on {int((w == 1.0).sum())} days")
    dt = cm.groupby("first").size()
    print(f"  roll periods: {len(dt)}, trading days each: min {dt.min()}, median {dt.median():.0f}, max {dt.max()}")
    vx_days = set(cm.index)
    vix_days = set(v.loc[cm.index.min():cm.index.max()].index)
    print(f"  VX days without a VIX close: {len(vx_days - vix_days)} {sorted(d.date() for d in vx_days - vix_days)[:6]}; "
          f"VIX days without the 30-day series: {len(vix_days - vx_days)} {sorted(d.date() for d in vix_days - vx_days)[:6]}")  # fmt: skip

    print("Known events (the 30-day series only; no strategy):")
    for day in ("2007-02-27", "2008-10-15", "2010-05-06", "2011-08-08", "2015-08-24", "2018-02-05", "2018-02-06",
                "2020-03-09", "2020-03-12", "2020-03-16", "2024-08-05"):  # fmt: skip
        t = pd.Timestamp(day)
        if t in cm.index:
            r = cm.at[t, "er_return"]
            print(f"  {day}: one-day roll return {r:+.1%}; 30-day price {cm.at[t, 'cm30_price']:.2f}, VIX {v.get(t, np.nan):.2f}")
    for a, b in (("2008-09-30", "2008-10-24"), ("2008-09-30", "2008-11-20"), ("2018-02-01", "2018-02-09"),
                 ("2020-02-19", "2020-03-16"), ("2020-02-19", "2020-03-23")):  # fmt: skip
        seg = cm.loc[pd.Timestamp(a) + pd.Timedelta(days=1):b, "er_return"]
        print(f"  {a} to {b}: roll return {(1 + seg).prod() - 1:+.1%} over {len(seg)} days; "
              f"largest day {seg.max():+.1%}")  # fmt: skip
    top = cm["er_return"].nlargest(5)
    print("  the five largest one-day rises:", ", ".join(f"{t.date()} {r:+.1%}" for t, r in top.items()))
    print(f"  VIX: {v.index.min().date()} to {v.index.max().date()}")


if __name__ == "__main__":
    fetch()
    checks()
