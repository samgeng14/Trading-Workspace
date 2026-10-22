"""
data.py — R6's data and its checks: EIA's weekly US crude oil inventories, the WTI
spot price, the NYMEX futures prices EIA published until 2024-04-05, EIA's archive of
its Weekly Petroleum Status Reports (when each week's figures came out, and what they
said then), and USO's prices. Phase 1 of the study looks at these checks only: no
rule's return, and no USO return on any signal.

    python data.py           # fetch what isn't cached yet, then print every check
    python data.py --fetch   # fetch only

Sources, all free and keyless, cached in research/_data/eia/ (git-ignored):
  * EIA's bulk petroleum file, opendata/bulk/PET.zip (JSON lines, read with the
    standard library). The weekly series: WCESTUS1, US ending stocks of crude oil
    excluding the Strategic Petroleum Reserve (SPR); W_EPC0_SAX_YCUOK_MBBL, the same
    at Cushing, Oklahoma, where NYMEX's WTI contract delivers; WCRRIUS2, refiners'
    net input of crude oil. The daily series: RWTC, WTI's spot price at Cushing, and
    RCLC1 to RCLC4, NYMEX's contracts 1 to 4, which EIA stopped publishing after
    2024-04-05.
  * EIA's series pages (dnav/pet/hist/LeafHandler.ashx): the same weekly series as
    HTML tables, a second copy (``check_second_copy``).
  * EIA's archive of the Weekly Petroleum Status Report
    (petroleum/supply/weekly/archive/). Every report from 2011-08-03 is in a folder
    named for its release date; a folder ending "_data" is named for the week its
    figures cover, released late. From each report: table 4 (CSV) as published,
    and, for a sample, the highlights (PDF), which state how far crude stocks stood
    from "the five year average for this time of year". The PDFs are read with
    Ghostscript (`gs`, -dSAFER) when it is installed; that check is skipped
    otherwise.
  * the ETF master and the vendor files: USO; BIL with its income through
    research/r5_bond_carry/data.py's tr_closes.
Downloads are data only: parsed, never run.
"""
from __future__ import annotations

import argparse
import csv
import functools
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[1]  # trading-engine
CACHE = HERE.parent / "_data" / "eia"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))  # for the NYSE calendar (src/common/clock.py)

_spec = importlib.util.spec_from_file_location("r5_data", HERE.parent / "r5_bond_carry" / "data.py")
r5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r5)

UA = "DV02 paper-fund research (github.com/samgeng14/Trading-Workspace)"
PAUSE_S = 1.0  # between requests: public servers, not ours
SITE = "https://www.eia.gov"
BULK_URL = SITE + "/opendata/bulk/PET.zip"
LEAF_URL = SITE + "/dnav/pet/hist/LeafHandler.ashx?n=PET&s={series}&f=W"
ARCHIVE_URL = SITE + "/petroleum/supply/weekly/archive/"

SERIES = {
    "stocks": "PET.WCESTUS1.W",  # thousand barrels; lease stocks excluded throughout, as restated in 2016
    "stocks_with_lease": "PET.W_EPC0_SAX_NUS_MBBL.W",  # thousand barrels; as published until 2016-09-30
    "cushing": "PET.W_EPC0_SAX_YCUOK_MBBL.W",  # thousand barrels
    "inputs": "PET.WCRRIUS2.W",  # thousand barrels a day
    "spot": "PET.RWTC.D",  # dollars a barrel
    "c1": "PET.RCLC1.D",
    "c2": "PET.RCLC2.D",
    "c3": "PET.RCLC3.D",
    "c4": "PET.RCLC4.D",
}
LEAF = {"stocks": "WCESTUS1", "cushing": "W_EPC0_SAX_YCUOK_MBBL", "inputs": "WCRRIUS2"}

FIRST_DECISION, HOLDOUT_START, PANEL_END = r5.FIRST_DECISION, r5.HOLDOUT_START, r5.PANEL_END
STRESS = r5.STRESS  # R3's eight windows
LAG_DAYS = 7  # a decision reads only weeks that ended at least this many days before it
NORM_YEARS = 5  # the five-year average EIA's own report compares stocks with
MIN_NORM_YEARS = 3  # Cushing's series starts 2004-04-09: fewer than five years before 2009-04
FUTURES_LAST = pd.Timestamp("2024-04-05")  # EIA's last NYMEX futures price
# EIA's weekly commercial crude stocks included lease stocks (oil in tanks at producing
# leases, about 31 million barrels then) until the report of 2016-10-13, which covered
# the week ending 2016-10-07 without them and restated the history.
LAST_LEASE_WEEK = pd.Timestamp("2016-09-30")
LEASE_CHANGE_REPORT = pd.Timestamp("2016-10-13")
SPLIT = pd.Timestamp("2020-04-28")  # USO's 1-for-8 reverse split took effect after this close
SPLIT_RATIO = 8
PRE_WINDOW = (pd.Timestamp("2006-04-11"), pd.Timestamp("2007-12-31"))  # USO's history before the first decision
VOL_TARGET = 0.10  # PLAN §9: the book targets 8-10% a year
WORST_DAY_LIMIT = -0.12  # R3's worst-day tolerance, a criterion here too
CRASH_FALL = 0.40  # an oil crash: WTI spot falls at least this much from a closing high
CRASH_REBOUND = 0.25  # a fall ends when WTI rebounds this much from its low
SPOT_FLOOR = 1.0  # dollars: WTI spot settled at -36.98 on 2020-04-20; the zigzag floors it here
TRACKING_BEFORE = (pd.Timestamp("2008-01-02"), pd.Timestamp("2020-03-31"))  # USO in the front month
TRACKING_AFTER = (pd.Timestamp("2020-07-01"), FUTURES_LAST)  # USO across months


class NotThere(Exception):
    """The server says the file doesn't exist (404)."""


# ── fetching ─────────────────────────────────────────────────────────────


def _fetch(url: str, path: Path, *, page: bool = False, retries: int = 3) -> Path:
    """``url`` saved at ``path`` unless already there. A web page where data is expected
    is refused rather than cached (``page`` accepts one); a 404 raises NotThere."""
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            time.sleep(PAUSE_S)
            if not page and body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
                raise RuntimeError(f"{url} returned a web page, not data")
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
            return path
        except urllib.error.HTTPError as exc:
            time.sleep(PAUSE_S)
            if exc.code == 404:
                raise NotThere(url) from exc
            last = exc
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            last = exc
        time.sleep(3.0 * (attempt + 1))
    raise RuntimeError(f"couldn't fetch {url}: {last}")


# ── EIA's series ─────────────────────────────────────────────────────────


_BULK: dict | None = None


def bulk() -> tuple[dict[str, pd.Series], dict[str, dict]]:
    """Every series in SERIES from EIA's bulk file, and each one's metadata (name,
    units, last update)."""
    global _BULK
    if _BULK is not None:
        return _BULK
    path = _fetch(BULK_URL, CACHE / "PET.zip")
    want = {v: k for k, v in SERIES.items()}
    found = re.compile(rb'"series_id"\s*:\s*"([^"]+)"')
    series, meta = {}, {}
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as f:
        for line in f:
            m = found.search(line[:300])
            if not m or m.group(1).decode() not in want:
                continue
            o = json.loads(line)
            name = want[o["series_id"]]
            rows = [(d, v) for d, v in o["data"] if v is not None]
            s = pd.Series([float(v) for _, v in rows], index=pd.to_datetime([d for d, _ in rows], format="%Y%m%d"))
            series[name] = s.sort_index().rename(name)
            meta[name] = {k: o.get(k) for k in ("series_id", "name", "units", "last_updated")}
    missing = set(SERIES) - set(series)
    if missing:
        raise RuntimeError(f"EIA's bulk file lacks {sorted(missing)}")
    _BULK = (series, meta)
    return _BULK


def weekly(name: str) -> pd.Series:
    return bulk()[0][name]


def leaf(name: str) -> pd.Series:
    """The weekly series as EIA's series page tabulates it: rows by month, up to five
    (end date, value) pairs a row."""
    path = _fetch(LEAF_URL.format(series=LEAF[name]), CACHE / "leaf" / f"{LEAF[name]}.html", page=True)
    html = path.read_text(encoding="latin-1")
    values = {}
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        ym = re.search(r"class='B6'>(?:&nbsp;)*\s*(\d{4})-[A-Za-z]{3}", row)
        if not ym:
            continue
        year = int(ym.group(1))
        for d, v in re.findall(r"class='B5'>([^<]*)</td>\s*<td class='B3'>([^<]*)</td>", row):
            d, v = d.replace("&nbsp;", "").strip(), v.replace("&nbsp;", "").replace(",", "").strip()
            if d and v:
                month, day = (int(x) for x in d.split("/"))
                values[pd.Timestamp(year, month, day)] = float(v)
    return pd.Series(values, dtype=float).sort_index().rename(name)


# ── EIA's archive of the weekly report ───────────────────────────────────


def releases() -> pd.DataFrame:
    """Every report in EIA's archive: its folder, the date in the folder's name, and
    whether it is a late data-only release (named for its week, not its release)."""
    path = _fetch(ARCHIVE_URL, CACHE / "wpsr" / "archive.html", page=True)
    html = path.read_text(encoding="latin-1")
    folders = sorted(set(re.findall(r'href="(/petroleum/supply/weekly/archive/\d{4}/\d{4}_\d{2}_\d{2}(?:_data)?)/', html)))
    rows = []
    for folder in folders:
        m = re.search(r"/(\d{4})_(\d{2})_(\d{2})(_data)?$", folder)
        rows.append({"folder": folder, "date": pd.Timestamp(int(m[1]), int(m[2]), int(m[3])), "data_only": bool(m[4])})
    return pd.DataFrame(rows).sort_values(["date", "data_only"]).reset_index(drop=True)


def _number(cell: str) -> float:
    cell = cell.replace(",", "").strip()
    return float(cell) if cell else math.nan


def table4(folder: str) -> dict | None:
    """A report's table 4 as published: its week and the week before, and the stocks
    excluding the SPR and at Cushing for each (thousand barrels; the table prints
    million barrels to three decimals). None when the archive has no table 4."""
    name = folder.rsplit("/", 1)[-1]
    try:
        path = _fetch(SITE + folder + "/csv/table4.csv", CACHE / "wpsr" / name / "table4.csv")
    except NotThere:
        return None
    rows = list(csv.reader(path.read_text(encoding="latin-1").splitlines()))
    week, prior = (pd.to_datetime(h.strip(), format="%m/%d/%y") for h in rows[0][1:3])
    year_ago = pd.to_datetime(rows[0][4].strip(), format="%m/%d/%y")
    out = {"folder": folder, "week": week, "prior_week": prior, "year_ago_week": year_ago}
    for key, label in (("stocks", "commercial (excluding spr)"), ("cushing", "cushing")):
        cells = next((r for r in rows[1:] if r and r[0].strip().lower() == label), None)
        out[key] = round(_number(cells[1]) * 1000) if cells else math.nan
        out[f"{key}_prior"] = round(_number(cells[2]) * 1000) if cells else math.nan
        out[f"{key}_year_ago"] = round(_number(cells[4]) * 1000) if cells else math.nan
    return out


def table2(folder: str) -> dict | None:
    """A report's table 2 as published: refiners' crude oil input in its week and the
    week before (thousand barrels a day). None when the archive has no table 2."""
    name = folder.rsplit("/", 1)[-1]
    try:
        path = _fetch(SITE + folder + "/csv/table2.csv", CACHE / "wpsr" / name / "table2.csv")
    except NotThere:
        return None
    rows = list(csv.reader(path.read_text(encoding="latin-1").splitlines()))
    week, prior = (pd.to_datetime(h.strip(), format="%m/%d/%y") for h in rows[0][2:4])
    cells = next(r for r in rows[1:] if len(r) > 3 and r[1].strip().lower() == "crude oil inputs")
    return {"folder": folder, "week": week, "prior_week": prior, "inputs": _number(cells[2]), "inputs_prior": _number(cells[3])}


@functools.cache
def as_published() -> pd.DataFrame:
    """table4() for every report in the archive, fetched once."""
    rows = [t for t in (table4(f) for f in releases()["folder"]) if t is not None]
    return pd.DataFrame(rows)


def highlights_sample() -> pd.DataFrame:
    """The sample of reports whose highlights are read: the first regular report of each
    quarter in the archive."""
    r = releases()
    r = r[~r["data_only"]]
    return r.groupby(r["date"].dt.to_period("Q")).head(1).reset_index(drop=True)


def highlight(folder: str) -> dict | None:
    """EIA's own sentence in a report's highlights: "At X million barrels, U.S. crude oil
    inventories are about N% above (below) the five year average for this time of
    year", or "at the five year average". None when Ghostscript isn't installed or the
    text has no such sentence (the reports to 2018 compare with "the average range"
    instead).

    The PDF's two columns interleave in the text, so the price column's words can fall
    inside the sentence. Only the crude oil sentence comes before the first "five year
    average" after the level, and the price column gives dollars, not percent: the
    figure is the first "N%" in that stretch. Its direction is the word right after
    the number when it is "above" or "below", else the "above the" or "below the" that
    ends the stretch; "at the" or "near the" right after "are" means 0."""
    gs = shutil.which("gs")
    if gs is None:
        return None
    name = folder.rsplit("/", 1)[-1]
    try:
        pdf = _fetch(SITE + folder + "/pdf/highlights.pdf", CACHE / "wpsr" / name / "highlights.pdf")
    except NotThere:
        return None
    txt = pdf.with_suffix(".txt")
    if not txt.exists():
        subprocess.run([gs, "-q", "-dSAFER", "-dNOPAUSE", "-dBATCH", "-sDEVICE=txtwrite", f"-sOutputFile={txt}", str(pdf)],
                       check=True, capture_output=True, timeout=120)  # fmt: skip
    text = re.sub(r"\s+", " ", txt.read_text(errors="ignore"))
    level = re.search(r"At ([\d.]+) million barrels, U\.S\. crude oil inventories are", text)
    if not level:
        return None
    rest = text[level.end(): level.end() + 600]
    end = re.search(r"five[- ]year\s+average", rest)
    if not end:
        return None
    stretch = rest[: end.start()]
    pct = re.search(r"(\d+(?:\.\d+)?)%", stretch)
    adjacent = re.search(r"(\d+(?:\.\d+)?)%\s+(above|below)\b", stretch)
    tail = re.search(r"\b(above|below)\s+the\s*$", stretch)
    if adjacent:
        value, side = float(adjacent.group(1)), adjacent.group(2)
    elif pct and tail:
        value, side = float(pct.group(1)), tail.group(1)
    elif re.match(r"\s*(?:at|near)\s+the\b", stretch):
        value, side = 0.0, "at"
    else:
        return None
    signed = value * (-1 if side == "below" else 1)
    return {"folder": folder, "level_mb": float(level.group(1)), "eia_pct": signed, "eia_side": side}


# ── the signal's inputs ──────────────────────────────────────────────────


def five_year_norm(s: pd.Series, *, years: int = NORM_YEARS, min_years: int = MIN_NORM_YEARS) -> pd.Series:
    """Each week's norm: the mean of the series on the same calendar day in each of the
    previous ``years`` years (29 February read as the 28th), each value linearly
    interpolated between the weeks around it. That is EIA's documented method for its
    natural gas storage comparisons; its crude oil method isn't published, so
    ``check_norm_against_eia`` compares this one with EIA's own statements. Fewer
    years than ``years`` are averaged when the series is shorter, down to
    ``min_years``; below that, NaN."""
    s = s.dropna()
    days = (s.index - s.index[0]).days.to_numpy(dtype=float)
    values = s.to_numpy(dtype=float)
    first, last = s.index[0], s.index[-1]
    out = []
    for w in s.index:
        got = []
        for k in range(1, years + 1):
            try:
                past = w.replace(year=w.year - k)
            except ValueError:  # 29 February
                past = w.replace(year=w.year - k, day=28)
            if first <= past <= last:
                got.append(float(np.interp((past - first).days, days, values)))
        out.append(float(np.mean(got)) if len(got) >= min_years else math.nan)
    return pd.Series(out, index=s.index, name=f"{s.name}_norm")


def days_of_supply(stocks: pd.Series) -> pd.Series:
    """Days of crude oil at the current rate of refining: the stocks over refiners' net
    crude input in the same week."""
    i = weekly("inputs")
    common = stocks.index.intersection(i.index)
    return (stocks.loc[common] / i.loc[common]).rename("days_of_supply")


def gauge(s: pd.Series) -> pd.Series:
    """A measure against its five-year norm, as a fraction: below 0 is below the norm."""
    return (s / five_year_norm(s) - 1).rename(s.name)


def as_then(with_lease: pd.Series, without: pd.Series) -> pd.Series:
    """A gauge as it read when published: on the lease-inclusive basis for the weeks
    up to LAST_LEASE_WEEK, each against a norm on that basis, and on the restated
    basis after. A month-end decision reads a week ending at least 7 days before it, so
    no decision before the change's report (2016-10-13) reads a later week, and none
    after it an earlier one."""
    return pd.concat([with_lease.loc[:LAST_LEASE_WEEK], without.loc[LAST_LEASE_WEEK + timedelta(days=1):]])


@functools.cache
def gauges() -> dict[str, pd.Series]:
    """Each rule's gauge, as published at the time (as_then), and the primary's and V2's
    on today's restated series for comparison."""
    lease, today = weekly("stocks_with_lease"), weekly("stocks")
    stocks_then = as_then(gauge(lease), gauge(today)).rename("stocks")
    days_then = as_then(gauge(days_of_supply(lease)), gauge(days_of_supply(today))).rename("days_of_supply")
    return {
        "stocks": stocks_then,
        "cushing": gauge(weekly("cushing")),
        "days_of_supply": days_then,
        "stocks_today": gauge(today).rename("stocks_today"),
        "days_of_supply_today": gauge(days_of_supply(today)).rename("days_of_supply_today"),
    }


def known_week(t: pd.Timestamp, weeks: pd.DatetimeIndex) -> pd.Timestamp:
    """The latest week a decision at t's close may read: one that ended at least
    LAG_DAYS before t."""
    w = weeks[weeks <= t - timedelta(days=LAG_DAYS)]
    return w[-1]


def decision_dates() -> pd.DatetimeIndex:
    return r5.decision_dates()


@functools.cache
def decisions() -> pd.DataFrame:
    """On every decision day, the week each rule reads and its gauge: from the
    inventories alone."""
    g = gauges()
    days = decision_dates()
    rows = []
    for t in days:
        row = {"date": t}
        for name, s in g.items():
            w = known_week(t, s.dropna().index)
            row[f"{name}_week"], row[name] = w, float(s.loc[w])
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


# ── USO ──────────────────────────────────────────────────────────────────


def uso() -> pd.DataFrame:
    """USO from the master: its split-adjusted bars, and the price a share actually
    traded at (the master's close divided by 8 up to the split)."""
    m = r5.master("USO").copy()
    m["actual_close"] = np.where(m.index <= SPLIT, m["close"] / SPLIT_RATIO, m["close"])
    m["actual_high"] = np.where(m.index <= SPLIT, m["high"] / SPLIT_RATIO, m["high"])
    m["actual_low"] = np.where(m.index <= SPLIT, m["low"] / SPLIT_RATIO, m["low"])
    return m


def wti_expiry(year: int, month: int) -> pd.Timestamp:
    """NYMEX's last trading day for the WTI contract delivering in (year, month): three
    business days before the 25th of the month before, or before the business day
    preceding the 25th when that isn't one. NYSE's calendar stands in for NYMEX's."""
    from src.common import clock  # noqa: PLC0415

    prior = pd.Timestamp(year, month, 1) - pd.offsets.MonthBegin(1)
    d = pd.Timestamp(prior.year, prior.month, 25).date()
    while not clock.is_us_business_day(d):
        d -= timedelta(days=1)
    for _ in range(3):
        d -= timedelta(days=1)
        while not clock.is_us_business_day(d):
            d -= timedelta(days=1)
    return pd.Timestamp(d)


def contract_returns() -> pd.DataFrame:
    """Contracts 1 and 2's daily returns on the days each held the same contract as the
    day before: the day after each expiry, EIA's contract 1 becomes the old contract 2,
    and those days are left out."""
    s = bulk()[0]
    c = pd.concat([s["c1"], s["c2"]], axis=1, sort=True).loc["2007-06-01":FUTURES_LAST].dropna()
    expiries = sorted({wti_expiry(y, m) for y in range(2007, 2025) for m in range(1, 13)})
    after = {c.index[c.index.searchsorted(e, side="right")] for e in expiries if e < c.index[-1]}
    r = c.pct_change()
    r = r[(c > 0).all(axis=1) & (c.shift(1) > 0).all(axis=1)]
    return r.drop(index=[d for d in after if d in r.index]).dropna()


# ── the checks ───────────────────────────────────────────────────────────


def _d(t) -> str:
    return pd.Timestamp(t).date().isoformat()


def _months(index: pd.DatetimeIndex) -> str:
    """Runs of months, a decision in the same month as the one before counting as one."""
    months = sorted({(t.year, t.month) for t in pd.DatetimeIndex(index)})
    if not months:
        return "none"
    runs, start, prev = [], months[0], months[0]
    for m in months[1:]:
        if (m[0] - prev[0]) * 12 + m[1] - prev[1] != 1:
            runs.append((start, prev))
            start = m
        prev = m
    runs.append((start, prev))
    return ", ".join(f"{a[0]}-{a[1]:02d}" + (f" to {b[0]}-{b[1]:02d}" if b != a else "") for a, b in runs)


def check_coverage() -> list[str]:
    series, meta = bulk()
    out = ["EIA's bulk file (PET.zip):"]
    for name, s in series.items():
        out.append(f"  {name}: {meta[name]['series_id']}, \"{meta[name]['name']}\" ({meta[name]['units']}), "
                   f"{_d(s.index[0])} to {_d(s.index[-1])}, {len(s)} values; updated {meta[name]['last_updated']}")  # fmt: skip
    for name in ("stocks", "cushing", "inputs"):
        s = series[name].loc["2000-01-01":]
        gaps = s.index.to_series().diff().dt.days.dropna()
        bad = gaps[gaps != 7]
        out.append(f"  {name} from 2000: {len(s)} weeks, every week ending a Friday: {bool((s.index.dayofweek == 4).all())}; "
                   f"gaps other than 7 days: {len(bad)}" + (f" ({', '.join(f'{_d(t)} {int(g)}d' for t, g in bad.items())})" if len(bad) else ""))  # fmt: skip
    spot = series["spot"]
    neg = spot[spot <= 0]
    out.append(f"  WTI spot at or below 0: {', '.join(f'{_d(t)} {v:.2f}' for t, v in neg.items()) or 'never'}")
    for k in ("c1", "c2", "c3", "c4"):
        c = series[k]
        neg = c[c <= 0]
        out.append(f"  {k} at or below 0: {', '.join(f'{_d(t)} {v:.2f}' for t, v in neg.items()) or 'never'}")
    return out


def check_second_copy() -> list[str]:
    out = ["the bulk file against EIA's series pages (a second copy of the same database):"]
    for name in LEAF:
        a, b = weekly(name), leaf(name)
        common = a.index.intersection(b.index)
        diff = (a.loc[common] - b.loc[common]).abs()
        only_a, only_b = a.index.difference(b.index), b.index.difference(a.index)
        out.append(f"  {name}: {len(common)} weeks in both, {int((diff > 0).sum())} differ (largest {diff.max():.0f}); "
                   f"only in the bulk file {len(only_a)}" + (f" ({_d(only_a[0])} to {_d(only_a[-1])})" if len(only_a) else "")
                   + f", only on the page {len(only_b)}" + (f" ({_d(only_b[0])} to {_d(only_b[-1])})" if len(only_b) else ""))  # fmt: skip
    return out


def release_table() -> pd.DataFrame:
    """Each week's first publication in the archive: the earliest regular report whose
    table 4 has it as its week; for a week first out in a data-only release, the
    release date is unknown, and the first regular report that shows it (as the week
    before) bounds it."""
    pub = as_published().copy()
    r = releases().set_index("folder")
    pub["release"] = pub["folder"].map(r["date"])
    pub["data_only"] = pub["folder"].map(r["data_only"])
    regular = pub[~pub["data_only"]]
    first = regular.groupby("week")["release"].min()
    rows = []
    for w in sorted(set(pub["week"]) | set(pub["prior_week"][pub["prior_week"] >= pub["week"].min()])):
        if w in first.index:
            rows.append({"week": w, "release": first[w], "known": True})
        else:
            later = regular.loc[regular["prior_week"] == w, "release"]
            rows.append({"week": w, "release": later.min() if len(later) else pd.NaT, "known": False})
    return pd.DataFrame(rows).set_index("week")


def check_release_timing() -> list[str]:
    r = releases()
    out = [f"EIA's archive of the weekly report: {len(r)} reports, {_d(r['date'].iloc[0])} to {_d(r['date'].iloc[-1])}, "
           f"{int(r['data_only'].sum())} late data-only releases ({', '.join(f.rsplit('/', 1)[-1] for f in r.loc[r['data_only'], 'folder'])})"]  # fmt: skip
    regular = r[~r["data_only"]]
    out.append("  regular releases by weekday: " + ", ".join(f"{k} {v}" for k, v in regular["date"].dt.day_name().value_counts().items()))
    rel = release_table()
    lag = (rel["release"] - rel.index).dt.days
    known = lag[rel["known"]]
    out.append(f"  days from a week's end to its first release (regular reports, {len(known)} weeks from {_d(known.index[0])}): "
               + ", ".join(f"{int(k)} days {int(v)}" for k, v in known.value_counts().sort_index().items()))  # fmt: skip
    late = known[known > 6]
    out.append("  released more than 6 days after the week ended: " + ", ".join(f"{_d(w)} ({int(d)}d)" for w, d in late.items()))
    pub = as_published().copy()
    pub["release"] = pub["folder"].map(r.set_index("folder")["date"])
    pub = pub.sort_values("release")
    repeats = pub[pub["week"].duplicated(keep="first")]
    out.append("  reports whose table 4 repeats an earlier report's week: "
               + (", ".join(f"{f.rsplit('/', 1)[-1]} (the week ending {_d(w)})" for f, w in zip(repeats["folder"], repeats["week"]))
                  or "none"))  # fmt: skip
    unknown = rel[~rel["known"]]
    out.append("  weeks no regular report has as its own (a data-only release, or a repeated table; the next report shows "
               "them as the week before): " + ", ".join(f"{_d(w)} (by {_d(t)})" for w, t in unknown["release"].items()))  # fmt: skip
    # the rule's week on each decision day the archive covers: was it out by the close?
    days = decision_dates()
    weeks = weekly("stocks").index
    checked = stale = unpublished = 0
    for t in days[days >= rel.index[0] + timedelta(days=14)]:
        w = known_week(t, weeks)
        if w not in rel.index or pd.isna(rel.loc[w, "release"]):
            continue
        checked += 1
        if rel.loc[w, "release"] > t:
            unpublished += 1
        out_by_t = rel.index[(rel["release"] <= t) & rel["known"]]
        if len(out_by_t) and out_by_t.max() > w:
            stale += 1
    out.append(f"  on {checked} decision days from 2011-08: the rule's week (ended {LAG_DAYS}+ days before) was out by the close "
               f"on {checked - unpublished}; a later week was already out on {stale} (the rule reads a week behind then)")  # fmt: skip
    return out


def check_revisions() -> list[str]:
    pub = as_published().copy()
    r = releases().set_index("folder")
    pub["data_only"] = pub["folder"].map(r["data_only"])
    pub["release"] = pub["folder"].map(r["date"])
    out = ["revisions: each week as first published in the archive against EIA's series today, and against the next report:"]
    lease, today = weekly("stocks_with_lease"), weekly("stocks")
    first = pub.dropna(subset=["stocks"]).sort_values("folder").groupby("week").first()
    for label, series, span in (("stocks, against today's series (lease stocks excluded)", today, slice(None)),
                                ("stocks to 2016-09-30, against the lease-inclusive series", lease, slice(None, LAST_LEASE_WEEK)),
                                ("stocks from 2016-10-07, against today's series", today, slice(LAST_LEASE_WEEK + timedelta(days=1), None))):  # fmt: skip
        f = first.loc[span]
        common = f.index.intersection(series.index)
        d = f.loc[common, "stocks"] - series.loc[common]
        nz = d[d != 0]
        out.append(f"  {label}: {len(common)} weeks ({_d(common[0])} to {_d(common[-1])}); differ on {len(nz)}"
                   + (f", by {nz.min():+,.0f} to {nz.max():+,.0f} thousand barrels" if len(nz) else ""))  # fmt: skip
    nxt = pub.dropna(subset=["stocks_prior"]).sort_values("folder").groupby("prior_week")["stocks_prior"].first()
    both = first.index.intersection(nxt.index)
    d2 = first.loc[both, "stocks"] - nxt.loc[both]
    nz2 = d2[d2 != 0]
    out.append(f"  stocks: the next report's \"week before\" differs from the first publication on {len(nz2)} of {len(both)} weeks"
               + (": " + ", ".join(f"{_d(w)} {v:+,.0f}" for w, v in nz2.items()) if len(nz2) else ""))  # fmt: skip
    after = pub[pub["release"] >= LEASE_CHANGE_REPORT].sort_values("folder").iloc[0]
    ya = after["year_ago_week"]
    out.append(f"  the first report without lease stocks ({_d(after['release'])}, the week ending {_d(after['week'])}) printed the "
               f"year-ago week ({_d(ya)}) at {after['stocks_year_ago']:,.0f}: today's series has {today.loc[ya]:,.0f}, the "
               f"lease-inclusive one {lease.loc[ya]:,.0f}; so the restated history was out with the change")  # fmt: skip
    gap = (lease - today.reindex(lease.index)).dropna()
    out.append("  lease stocks (the two series' gap), yearly means: " + ", ".join(
        f"{y} {v / 1000:.1f}M" for y, v in gap.groupby(gap.index.year).mean().items() if y in (1985, 1995, 2005, 2008, 2010, 2012, 2014, 2016)))  # fmt: skip
    c = pub.dropna(subset=["cushing"]).sort_values("folder").groupby("week").first()
    cc = c.index.intersection(weekly("cushing").index)
    dc = c.loc[cc, "cushing"] - weekly("cushing").loc[cc]
    out.append(f"  Cushing: {len(cc)} weeks ({_d(cc[0])} to {_d(cc[-1])}); differ from today's figure on {int((dc != 0).sum())}")
    rows = [t for t in (table2(f) for f in highlights_sample()["folder"]) if t is not None]
    if rows:
        t2 = pd.DataFrame(rows).set_index("week")
        i = weekly("inputs")
        ci = t2.index.intersection(i.index)
        di = (t2.loc[ci, "inputs"] - i.loc[ci]).abs()
        out.append(f"  refiners' crude input (table 2, the first report each quarter): {len(ci)} weeks, differ from today's "
                   f"figure on {int((di > 0).sum())}" + (f" (largest {di.max():,.0f} thousand barrels a day)" if (di > 0).any() else ""))  # fmt: skip
    return out


def check_norm_against_eia() -> list[str]:
    if shutil.which("gs") is None:
        return ["EIA's own five-year comparison: skipped (Ghostscript isn't installed)"]
    sample = highlights_sample()
    stocks, norm = weekly("stocks"), five_year_norm(weekly("stocks"))
    rows = []
    for folder, released in zip(sample["folder"], sample["date"]):
        h = highlight(folder)
        if h is None:
            continue
        # the report's week: the last Friday at least 5 days before its release (table 4's own
        # week, except where the archive repeats an earlier table, as for 2019-07-03)
        w = released - timedelta(days=5)
        w -= timedelta(days=(w.dayofweek - 4) % 7)
        if w not in norm.index:
            continue
        mine = (stocks.loc[w] / norm.loc[w] - 1) * 100
        rows.append({**h, "week": w, "level_table4_mb": stocks.loc[w] / 1000, "mine_pct": mine})
    out = [f"EIA's own five-year comparison, from the highlights of the first report each quarter ({len(sample)} reports):"]
    if not rows:
        return out + ["  no report in the sample states one"]
    h = pd.DataFrame(rows)
    at = h["eia_side"] == "at"
    sign_ok = ((h["eia_side"] == "above") & (h["mine_pct"] > 0)) | ((h["eia_side"] == "below") & (h["mine_pct"] < 0)) | (
        at & (h["mine_pct"].abs() < 1.0))  # fmt: skip
    gap = h["mine_pct"] - h["eia_pct"]
    level_gap = (h["level_mb"] - h["level_table4_mb"]).abs()
    out.append(f"  {len(h)} state it, from the week ending {_d(h['week'].min())}; EIA's level matches the week's figure to 0.1 "
               f"million barrels in {int((level_gap <= 0.051).sum())}")  # fmt: skip
    out.append(f"  this study's norm puts stocks on EIA's side in {int(sign_ok.sum())} of {len(h)} (\"at the\" average: within 1%); "
               f"its percent minus EIA's, which EIA rounds to a whole percent: median {gap.median():+.2f} points, "
               f"within 1 point in {int((gap.abs() <= 1.0).sum())}, largest {gap.abs().max():.2f}")  # fmt: skip
    for _, x in h[~sign_ok | (gap.abs() > 1.0) | (level_gap > 0.051)].iterrows():
        out.append(f"    {_d(x['week'])}: EIA \"{x['eia_side']}\" {x['eia_pct']:+.1f}% at {x['level_mb']:.1f}M (the week's figure "
                   f"{x['level_table4_mb']:.1f}M); this study {x['mine_pct']:+.2f}%")  # fmt: skip
    return out


def check_signal() -> list[str]:
    d = decisions()
    out = ["what each rule would hold, from the inventories alone (gauge below 0: USO):"]
    for name, label in (("stocks", "primary: US stocks excluding the SPR, as published"), ("cushing", "V1: Cushing's stocks"),
                        ("days_of_supply", "V2: days of supply, as published")):  # fmt: skip
        g = d[name]
        wf, ho = g[g.index < HOLDOUT_START], g[g.index >= HOLDOUT_START]
        held = (g < 0).astype(int)
        out.append(f"  {label}: walk-forward {len(wf)} decisions, below the norm on {int((wf < 0).sum())} "
                   f"({_months(wf.index[wf < 0])}); holdout {len(ho)}, below on {int((ho < 0).sum())} "
                   f"({_months(ho.index[ho < 0])}); switches in all {int(held.diff().abs().sum())}")  # fmt: skip
        out.append(f"    the gauge over the walk-forward: median {wf.median() * 100:+.1f}%, 10th to 90th percentile "
                   f"{wf.quantile(0.1) * 100:+.1f}% to {wf.quantile(0.9) * 100:+.1f}%; within 1% of the norm on "
                   f"{int((wf.abs() < 0.01).sum())}")  # fmt: skip
    for then, now in (("stocks", "stocks_today"), ("days_of_supply", "days_of_supply_today")):
        differ = d.index[(d[then] < 0) != (d[now] < 0)]
        out.append(f"  {then} on today's restated series (lease stocks excluded throughout) instead: differs on {len(differ)} decisions"
                   + (": " + ", ".join(f"{_d(t)} ({d.loc[t, then] * 100:+.2f}% as published, {d.loc[t, now] * 100:+.2f}% today)" for t in differ)
                      if len(differ) else ""))  # fmt: skip
    agree = ((d["stocks"] < 0) == (d["cushing"] < 0)).mean()
    agree2 = ((d["stocks"] < 0) == (d["days_of_supply"] < 0)).mean()
    out.append(f"  the primary and V1 agree on {agree:.0%} of decisions, the primary and V2 on {agree2:.0%}")
    first = d.iloc[0]
    out.append(f"  the first decision ({_d(d.index[0])}) reads the week ending {_d(first['stocks_week'])}; "
               f"Cushing's norm then averages {_norm_years(weekly('cushing'), first['cushing_week'])} years")  # fmt: skip
    return out


def positions() -> pd.DataFrame:
    """Whether each rule is in USO over each session's return, from the inventories alone:
    a decision at t's close trades at the next session's close (lag 1), so it holds from
    the session after that."""
    d = decisions()
    nyse = r5.sessions()
    nyse = nyse[(nyse >= d.index[0]) & (nyse <= PANEL_END)]
    out = {}
    for name in ("stocks", "cushing", "days_of_supply"):
        held = pd.Series(np.nan, index=nyse)
        for t, g in d[name].items():
            i = nyse.searchsorted(t, side="right") + 1  # the trade fills at the next close; held from the session after
            if i < len(nyse):
                held.iloc[i:] = float(g < 0)
        out[name] = held.fillna(0.0)
    return pd.DataFrame(out)


def check_positions_in_windows() -> list[str]:
    p = positions()
    wins = [(f"{k} ({a} to {b})", pd.Timestamp(a), pd.Timestamp(b)) for k, (a, b) in STRESS.items()]
    wins += [(f"oil crash {_d(a)} to {_d(b)}", a, b) for a, b, _ in crashes() if b >= FIRST_DECISION]
    out = ["the share of each window's sessions each rule spends in USO (positions only, from the inventories):"]
    for label, a, b in wins:
        x = p.loc[a:b]
        out.append(f"  {label}: primary {x['stocks'].mean():.0%}, V1 {x['cushing'].mean():.0%}, V2 {x['days_of_supply'].mean():.0%}")
    wf, ho = p[p.index < HOLDOUT_START], p[p.index >= HOLDOUT_START]
    out.append(f"  over the walk-forward: primary {wf['stocks'].mean():.0%}, V1 {wf['cushing'].mean():.0%}, V2 {wf['days_of_supply'].mean():.0%}; "
               f"over the holdout: {ho['stocks'].mean():.0%}, {ho['cushing'].mean():.0%}, {ho['days_of_supply'].mean():.0%}")  # fmt: skip
    return out


def _norm_years(s: pd.Series, w: pd.Timestamp) -> int:
    return sum(1 for k in range(1, NORM_YEARS + 1) if w.replace(year=w.year - k) >= s.dropna().index[0])


def check_mechanism() -> list[str]:
    """The theory of storage's link, from prices and inventories only: on the decision
    days to EIA's last futures price, the curve's slope (contract 1 over contract 2,
    less 1) against each gauge."""
    d = decisions()
    s = bulk()[0]
    c = pd.concat([s["c1"], s["c2"]], axis=1, sort=True).dropna()
    days = d.index[d.index <= FUTURES_LAST]
    slope = pd.Series([float((c.loc[:t].iloc[-1]["c1"] / c.loc[:t].iloc[-1]["c2"]) - 1) for t in days], index=days)
    out = [f"the curve against the gauges, on {len(days)} decision days to {_d(days[-1])} (slope: contract 1 / contract 2 - 1; "
           f"backwardation above 0):"]  # fmt: skip
    for name in ("stocks", "cushing", "days_of_supply"):
        g = d.loc[days, name]
        below, back = g < 0, slope > 0
        out.append(f"  {name}: correlation with the slope {np.corrcoef(g, slope)[0, 1]:+.2f}; below the norm {int(below.sum())} "
                   f"(backwardated on {int((below & back).sum())}), above it {int((~below).sum())} (backwardated on "
                   f"{int((~below & back).sum())})")  # fmt: skip
    out.append(f"  the slope: backwardated on {int((slope > 0).sum())} of {len(days)}; median {slope.median() * 100:+.2f}%, "
               f"lowest {slope.min() * 100:+.1f}% ({_d(slope.idxmin())}), highest {slope.max() * 100:+.1f}% ({_d(slope.idxmax())})")  # fmt: skip
    return out


def check_uso() -> list[str]:
    m = uso()
    v = r5.vendor("USO")
    a = r5.ibkr_adjusted("USO")
    out = [f"USO: the master from {_d(m.index[0])} to {_d(m.index[-1])}, {len(m)} sessions"]
    common = m.index.intersection(v.index)
    ratio = m.loc[common, "close"] / v.loc[common, "close"]
    pre, post = ratio[ratio.index <= SPLIT], ratio[ratio.index > SPLIT]
    out.append(f"  the master's closes over the vendor's: {pre.min():.4f} to {pre.max():.4f} up to {_d(SPLIT)} ({len(pre)} sessions), "
               f"{post.min():.4f} to {post.max():.4f} after ({len(post)}): both are split-adjusted")  # fmt: skip
    vr = m.loc[common, "volume"] / v.loc[common, "volume"]
    out.append(f"  the master's volumes over the vendor's: median {vr[vr.index <= SPLIT].median():.4f} up to the split, "
               f"{vr[vr.index > SPLIT].median():.4f} after; so shares are split-adjusted too, and close times volume is the "
               f"dollars traded")  # fmt: skip
    around = m.loc[SPLIT - pd.Timedelta(days=6): SPLIT + pd.Timedelta(days=4), ["close", "actual_close"]]
    out.append("  around the split (master close, the price a share traded at): "
               + ", ".join(f"{_d(t)} {r['close']:.2f} ({r['actual_close']:.2f})" for t, r in around.iterrows()))  # fmt: skip
    tr_gap = (v["tr"] / v["close"]).dropna()
    out.append(f"  distributions: the vendor's total-return index over its price ranges {tr_gap.min():.6f} to {tr_gap.max():.6f} "
               f"over {len(tr_gap)} sessions to {_d(v.index[-1])}" + ("; USO paid nothing" if (tr_gap - 1).abs().max() < 1e-9 else ""))  # fmt: skip
    ac = (a / m["close"].reindex(a.index)).dropna()
    out.append(f"  IBKR's adjusted closes over the master's, from {_d(ac.index[0])}: {ac.min():.6f} to {ac.max():.6f}")
    raw = pd.read_csv(r5.VENDOR / "USO.csv", encoding="utf-8-sig", dtype=str)
    nav_ps = pd.Series(pd.to_numeric(raw["NAV"].str.replace(",", ""), errors="coerce").to_numpy(),
                       index=pd.to_datetime(raw["Date"], format="%m/%d/%y"))  # fmt: skip
    nav = (v["close"] / nav_ps.reindex(v.index) - 1).dropna().loc[FIRST_DECISION:]
    out.append(f"  the close's premium over NAV from {_d(FIRST_DECISION)} (vendor): median {nav.median() * 1e4:+.1f} bps, 1st to 99th "
               f"percentile {nav.quantile(0.01) * 1e4:+.0f} to {nav.quantile(0.99) * 1e4:+.0f} bps; largest "
               f"{', '.join(f'{_d(t)} {x * 1e4:+.0f}' for t, x in nav.abs().nlargest(3).items())} (absolute bps)")  # fmt: skip
    return out


def check_tracking() -> list[str]:
    """USO's daily returns against contracts 1 and 2's on the same days (no roll days):
    which part of the curve USO held, before and after April 2020. Unconditional: no
    signal enters."""
    m = uso()
    r_uso = m["close"].pct_change()
    cr = contract_returns()
    out = ["USO against the curve (daily returns; contract roll days left out):"]
    for label, (a, b) in (("front month, 2008-01 to 2020-03", TRACKING_BEFORE), ("across months, 2020-07 to 2024-04", TRACKING_AFTER)):
        x = pd.concat([r_uso.rename("uso"), cr], axis=1, sort=True).loc[a:b].dropna()
        parts = []
        for k in ("c1", "c2"):
            beta = float(np.cov(x["uso"], x[k])[0, 1] / np.var(x[k], ddof=1))
            parts.append(f"on {k} beta {beta:.2f}, R² {np.corrcoef(x['uso'], x[k])[0, 1] ** 2:.3f}")
        out.append(f"  {label} ({len(x)} days): " + "; ".join(parts))
    return out


def check_size() -> list[str]:
    m = uso()
    r = m["close"].pct_change().dropna()
    pre = r.loc[PRE_WINDOW[0]: PRE_WINDOW[1]]
    vol = float(pre.std(ddof=1) * math.sqrt(252))
    size = VOL_TARGET / vol
    out = [f"the size: USO's volatility before the first decision ({_d(pre.index[0])} to {_d(pre.index[-1])}, {len(pre)} days) "
           f"{vol:.1%} a year; the fund's target of {VOL_TARGET:.0%} over it is {size:.1%} of NAV"]  # fmt: skip
    worst = r.loc[FIRST_DECISION:].nsmallest(5)
    out.append("  USO's five worst days from 2008: " + ", ".join(f"{_d(t)} {x:+.1%}" for t, x in worst.items())
               + f"; at {size:.1%} of NAV the worst is {worst.iloc[0] * size:+.1%} of NAV, against the {WORST_DAY_LIMIT:.0%} limit")  # fmt: skip
    return out


def crashes() -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    """Oil's crashes: every fall in WTI spot of CRASH_FALL or more from a closing high to
    a closing low, the swings split where the price rebounds CRASH_REBOUND from its low
    (a zigzag; the price floored at SPOT_FLOOR, since it went below 0 on 2020-04-20)."""
    p = weekly("spot").clip(lower=SPOT_FLOOR)
    out = []
    hi = lo = p.index[0]
    falling = False
    for t, x in p.items():
        if not falling:
            if x > p[hi]:
                hi = t
            if x <= p[hi] * (1 - CRASH_REBOUND):  # a fall has begun
                falling, lo = True, t
        else:
            if x < p[lo]:
                lo = t
            if x >= p[lo] * (1 + CRASH_REBOUND):  # the fall has ended
                out.append((hi, lo, float(p[lo] / p[hi] - 1)))
                falling, hi = False, t
    if falling:
        out.append((hi, lo, float(p[lo] / p[hi] - 1)))
    return [(a, b, f) for a, b, f in out if f <= -CRASH_FALL]


def check_crashes() -> list[str]:
    spot = weekly("spot")
    found = [(a, b, f) for a, b, f in crashes() if b >= pd.Timestamp("2007-06-01")]
    out = [f"oil's crashes: falls in WTI spot of {CRASH_FALL:.0%} or more, high to low, split at {CRASH_REBOUND:.0%} rebounds "
           f"(the price floored at ${SPOT_FLOOR:.0f}):"]  # fmt: skip
    for a, b, f in found:
        where = "walk-forward" if b < HOLDOUT_START else ("holdout" if a >= HOLDOUT_START else "straddles the holdout's start")
        out.append(f"  {_d(a)} ${spot[a]:.2f} to {_d(b)} ${spot[b]:.2f}: {f:+.0%} ({where})")
    return out


def check_costs() -> list[str]:
    m = uso().loc[FIRST_DECISION:PANEL_END]
    px = m["actual_close"]
    by_year = []
    for y, g in m.groupby(m.index.year):
        p = g["actual_close"]
        half_cent = 50 / p  # bps: half of a one-cent spread
        commission = 50 / p  # bps: $0.005 a share
        harness = 50 / g["close"]  # bps: what the harness charges, on the split-adjusted price
        by_year.append(f"{y}: ${p.min():.2f} to ${p.max():.2f} (median ${p.median():.2f}); half a cent {half_cent.median():.2f} bps "
                       f"and $0.005 a share {commission.median():.2f} bps at the median, the harness {harness.median():.2f}; "
                       f"median range {((g['actual_high'] - g['actual_low']) * 100).median():.0f} cents; median "
                       f"${(g['close'] * g['volume']).median() / 1e6:,.0f}M a day")  # fmt: skip
    out = [f"costs: USO's actual share price from {_d(FIRST_DECISION)} ran ${px.min():.2f} ({_d(px.idxmin())}) to ${px.max():.2f} "
           f"({_d(px.idxmax())}); the master's split-adjusted closes are 8 times the actual up to {_d(SPLIT)}, so the harness's "
           f"per-share commission is an eighth of the true one there:"]  # fmt: skip
    out += ["  " + line for line in by_year]
    allin = (100 / px)  # half a cent plus $0.005, in bps
    out.append(f"  half a cent plus $0.005 a share, in bps of the actual price: median {allin.median():.1f}, 90th percentile "
               f"{allin.quantile(0.9):.1f}, 99th {allin.quantile(0.99):.1f}, highest {allin.max():.1f} ({_d(allin.idxmax())})")  # fmt: skip
    b = r5.master("BIL").loc[FIRST_DECISION:PANEL_END]
    out.append(f"  BIL: ${b['close'].min():.2f} to ${b['close'].max():.2f}; median ${(b['close'] * b['volume']).median() / 1e6:,.0f}M a day")
    return out


def checks() -> None:
    lines = check_coverage() + check_second_copy() + check_release_timing() + check_revisions()
    lines += check_norm_against_eia() + check_signal() + check_mechanism() + check_uso() + check_tracking()
    lines += check_size() + check_crashes() + check_positions_in_windows() + check_costs()
    print("\n".join(lines))


def fetch() -> None:
    bulk()
    for name in LEAF:
        leaf(name)
    r = releases()
    for i, folder in enumerate(r["folder"]):
        table4(folder)
        if (i + 1) % 100 == 0:
            print(f"  table 4: {i + 1} of {len(r)}", flush=True)
    for folder in highlights_sample()["folder"]:
        highlight(folder)
        table2(folder)
    print("fetched", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="fetch only")
    if parser.parse_args().fetch:
        fetch()
    else:
        checks()
