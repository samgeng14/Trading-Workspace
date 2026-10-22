"""
fetch.py — R1's free data, downloaded once into research/_data/ (git-ignored) and
read from there after. Every source is official and needs no key or login:

  * the Federal Reserve's FOMC calendars: each scheduled meeting's last day, the
    day its statement is released (federalreserve.gov, 1994 onwards). The Fed
    publishes the next year's schedule well ahead, so the dates are known in
    advance.
  * ALFRED (St. Louis Fed): every date the Employment Situation (release 50) and
    the Consumer Price Index (release 10) were published. BLS publishes each
    year's release schedule ahead of the year.
  * the NYSE calendar, from the rules in trading-engine/src/common/clock.py: the
    sessions known in advance (it doesn't know unscheduled closures, and neither
    could a trader beforehand).

    python fetch.py        # fetch what isn't cached yet, then print the data checks

Downloaded files are data only: they are parsed, never executed.
"""
from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[1]  # trading-engine
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
from src.common import clock  # noqa: E402

DATA = HERE.parent / "_data"
UA = "DV02 paper-fund research (github.com/samgeng14/Trading-Workspace)"
PAUSE_S = 1.0  # between requests: public servers, not ours
FIRST_YEAR = 1994  # the FOMC has announced its decisions since February 1994
LAST_HISTORICAL = 2020  # the historical pages end here; fomccalendars.htm has 2021 on
ALFRED_DATES = "https://alfred.stlouisfed.org/release/downloaddates?rid={rid}&ff=txt"
EMPLOYMENT, CPI = 50, 10  # ALFRED's release ids

# Releases BLS moved after a lapse in appropriations, announced only days before
# they fell; whether two sessions' notice was given isn't in the data, so the rules
# leave these days out (HYPOTHESIS.md). Found by the checks below: an Employment
# Situation off its weekday, or a release more than 40 days after the one before
# that a shutdown explains.
RESCHEDULED = {
    EMPLOYMENT: [date(2013, 10, 22), date(2025, 11, 20), date(2025, 12, 16), date(2026, 2, 11)],
    CPI: [date(2013, 10, 30), date(2025, 10, 24), date(2025, 12, 18)],
}


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
            if path.suffix in (".csv", ".txt") and body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
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


# ── the FOMC's scheduled announcements ───────────────────────────────────

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], start=1)}  # fmt: skip
MONTH3 = {m[:3]: i for m, i in MONTHS.items()}


def _last_day(year: int, text: str) -> date:
    """The last day of a meeting written like "January 29-30", "January 31-February 1",
    "Jan 31-Feb 1" or "March 18": the last month and day named, in order."""
    month, last = None, None
    for name, num in re.findall(r"([A-Za-z]{3,})|(\d{1,2})", text):
        if name and name[:3].title() in MONTH3:
            month = MONTH3[name[:3].title()]
        elif num and month:
            last = (month, int(num))
    if last is None:
        raise ValueError(f"no meeting date in {text!r}")
    return date(year, *last)


def _fomc_rows() -> tuple[list[dict], list[str]]:
    """(scheduled announcements, what was left out) from the Fed's pages."""
    rows, dropped = [], []
    for year in range(FIRST_YEAR, LAST_HISTORICAL + 1):
        page = _get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm",
                    DATA / "fed" / f"fomchistorical{year}.htm").read_text(errors="ignore")  # fmt: skip
        for heading in re.findall(r"<h5[^>]*>(.*?)</h5>", page, re.S):
            label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", heading)).strip()
            if " Meeting" not in label or any(w in label.lower() for w in ("unscheduled", "cancel")):
                dropped.append(f"{year}: {label}")
                continue  # conference calls, unscheduled meetings, and 2020-03-17/18, cancelled
            rows.append({"date": _last_day(year, label.split(" Meeting")[0]), "year": year, "label": label})
    page = _get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                DATA / "fed" / "fomccalendars.htm").read_text(errors="ignore")  # fmt: skip
    for block in re.split(r"(?=<h4[^>]*>\s*<a[^>]*>\s*\d{4} FOMC Meetings)", page)[1:]:
        year = int(re.search(r"(\d{4}) FOMC Meetings", block).group(1))
        if year <= LAST_HISTORICAL:
            continue
        for month, days in re.findall(
            r'fomc-meeting__month[^>]*>\s*(?:<[^>]+>\s*)*([A-Za-z/]+)\s*(?:</[^>]+>\s*)*</div>.*?'
            r'fomc-meeting__date[^>]*>\s*(.*?)</div>', block, re.S):  # fmt: skip
            days_text = re.sub(r"<[^>]+>", " ", days)
            if "unscheduled" in days_text.lower() or "notation" in days_text.lower():
                dropped.append(f"{year}: {month} {' '.join(days_text.split())}")
                continue
            nums = re.findall(r"\d{1,2}", days_text)
            if not nums:
                continue
            last_month = month.split("/")[-1]  # "Apr/May" meetings end in the second month
            month_name = next(m for m in MONTHS if m.startswith(last_month[:3]))
            rows.append({"date": date(year, MONTHS[month_name], int(nums[-1])), "year": year,
                         "label": f"{month} {re.sub(r'[^0-9-]', '', days_text)} Meeting - {year}"})  # fmt: skip
    return rows, dropped


def fomc_announcements() -> pd.DataFrame:
    """Every scheduled FOMC meeting's announcement day from 1994: (date, year, label).
    Conference calls and meetings marked unscheduled are left out."""
    out = DATA / "fed" / "fomc_announcements.csv"
    if out.exists():
        return pd.read_csv(out, parse_dates=["date"])
    rows, _ = _fomc_rows()
    df = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    # one meeting listed as two days (2003-09-15 and -16): keep its last, the announcement
    df = df[~(df["date"].shift(-1) - df["date"] <= pd.Timedelta(days=2))].reset_index(drop=True)
    df.to_csv(out, index=False)
    return df


# ── BLS release dates, from ALFRED ───────────────────────────────────────


def release_dates(rid: int) -> list[date]:
    """Every date ALFRED records the release ``rid`` as published, sorted."""
    path = _get(ALFRED_DATES.format(rid=rid), DATA / "alfred" / f"release_dates_{rid}.txt")
    text = path.read_text()
    return sorted({date.fromisoformat(d) for d in re.findall(r"^\s*(\d{4}-\d{2}-\d{2})\s*$", text, re.M)})


def monthly_releases(rid: int) -> list[date]:
    """One release a month: the report itself, not the other vintages ALFRED keeps.

    - Employment Situation: the first date in each month. The later ones are
      corrections and benchmark revisions (2024-08-21's preliminary benchmark).
    - CPI: the last date in each month. February's earlier date is the revision
      of the seasonal factors, a few days before January's CPI.
    """
    by_month: dict[tuple[int, int], list[date]] = {}
    for d in release_dates(rid):
        by_month.setdefault((d.year, d.month), []).append(d)
    pick = min if rid == EMPLOYMENT else max
    return sorted(pick(v) for v in by_month.values())


# ── the NYSE calendar ─────────────────────────────────────────────────────


def sessions(start: str, end: str) -> list[date]:
    """NYSE sessions by clock.py's rules, as a trader knew them in advance."""
    days = pd.date_range(start, end, freq="D")
    return [d.date() for d in days if clock.is_us_business_day(d.date())]


def next_session(d: date) -> date:
    """``d`` if it is a session, else the first session after it."""
    while not clock.is_us_business_day(d):
        d += timedelta(days=1)
    return d


def turn_of_month(days: list[date]) -> set[date]:
    """The turn of the month (Lakonishok and Smidt 1988, McConnell and Xu 2008):
    each month's last session and the next month's first three."""
    out: set[date] = set()
    by_month: dict[tuple[int, int], list[date]] = {}
    for d in days:
        by_month.setdefault((d.year, d.month), []).append(d)
    months = sorted(by_month)
    for prev, nxt in zip(months, months[1:]):
        out.add(by_month[prev][-1])
        out.update(by_month[nxt][:3])
    return out


def announcement_days(days: list[date]) -> dict[str, set[date]]:
    """FOMC announcement days, and the sessions of each Employment Situation and CPI
    release (one on a non-session day, a Good Friday, moves to the next session),
    the rescheduled ones left out."""
    span = set(days)
    fomc = {d.date() for d in fomc_announcements()["date"]} & span
    out = {"fomc": fomc}
    for name, rid in (("employment", EMPLOYMENT), ("cpi", CPI)):
        keep = [d for d in monthly_releases(rid) if d not in RESCHEDULED[rid]]
        out[name] = {next_session(d) for d in keep} & span
    return out


# ── the data checks ──────────────────────────────────────────────────────


def checks() -> None:
    f = fomc_announcements()
    _, dropped = _fomc_rows()
    per_year = f.groupby("year").size()
    print(f"FOMC: {len(f)} scheduled announcements, {f['date'].min().date()} to {f['date'].max().date()}; "
          f"years without 8: {dict(per_year[per_year != 8])}")  # fmt: skip
    print(f"  left out: {len(dropped)} headings (conference calls, unscheduled, cancelled), e.g. {dropped[:3]}")
    print(f"  2020-03-15's emergency cut among them: {'2020-03-15' not in set(f['date'].dt.date.astype(str))}")
    off = [d.date() for d in f["date"] if not clock.is_us_business_day(d.date())]
    print(f"  announcement dates that aren't NYSE sessions: {off or 'none'}")

    for name, rid in (("Employment Situation", EMPLOYMENT), ("CPI", CPI)):
        every = release_dates(rid)
        monthly = monthly_releases(rid)
        dropped_same_month = sorted(set(d for d in every if d.year >= 2005) - set(monthly))
        print(f"{name} (ALFRED release {rid}): {len(every)} dates, {every[0]} to {every[-1]}; "
              f"one a month from 2005: {sum(d.year >= 2005 for d in monthly)}")  # fmt: skip
        print(f"  other dates the same month, not the report (from 2005): {[str(d) for d in dropped_same_month]}")
        months = {(d.year, d.month) for d in monthly}
        empty = [f"{y}-{m:02d}" for y in range(2005, 2027) for m in range(1, 13)
                 if (y, m) not in months and (y, m) <= (2026, 10)]  # fmt: skip
        print(f"  months without a release: {empty or 'none'}")
        gaps = [(b, (b - a).days) for a, b in zip(monthly, monthly[1:]) if b.year >= 2005 and (b - a).days > 40]
        print(f"  more than 40 days after the one before: {[f'{d} ({n} days)' for d, n in gaps]}")
        if rid == EMPLOYMENT:
            regular = [d for d in monthly if d.year >= 2005 and (d.weekday() == 4 or (
                d.weekday() == 3 and not clock.is_us_business_day(d + timedelta(days=1))))]  # fmt: skip
            odd = [str(d) for d in monthly if d.year >= 2005 and d not in regular]
            print(f"  off its weekday (a Friday, or the Thursday before a closed Friday): {odd}")
        print(f"  rescheduled, left out: {[str(d) for d in RESCHEDULED[rid]]}; "
              f"each in the data: {all(d in set(monthly) for d in RESCHEDULED[rid])}")  # fmt: skip
        moved = [f"{d} -> {next_session(d)}" for d in monthly if d.year >= 2005 and not clock.is_us_business_day(d)]
        print(f"  released on a day NYSE was closed, so counted on the next session: {moved or 'none'}")

    sys.path.insert(0, str(HERE.parent / "s1_trend"))
    import s1_trend as s1t

    panel, _, _ = s1t.load_panel()
    panel = panel.loc[:"2026-10-16"]
    have = {d.date() for d in panel.index}
    rule = set(sessions(str(panel.index[0].date()), str(panel.index[-1].date())))
    print(f"S1's panel: {panel.index[0].date()} to {panel.index[-1].date()}, {len(have)} sessions; "
          f"by clock.py's rules {len(rule)}")  # fmt: skip
    print(f"  sessions by the rules the panel lacks (closures nobody knew ahead): {sorted(str(d) for d in rule - have)}")
    print(f"  panel days the rules don't call sessions: {sorted(str(d) for d in have - rule) or 'none'}")

    days = sessions("2007-12-01", "2026-10-31")
    tom = turn_of_month(days)
    ann = announcement_days(days)
    books = {
        "primary, turn of the month": tom,
        "V1, + FOMC days": tom | ann["fomc"],
        "V2, + FOMC, jobs and CPI days": tom | ann["fomc"] | ann["employment"] | ann["cpi"],
    }
    print("Days in SPY by year (counts only, 2008 to 2026-10-16), and the share of sessions:")
    for name, chosen in books.items():
        per = pd.Series([d.year for d in chosen if date(2008, 1, 2) <= d <= date(2026, 10, 16)]).value_counts().sort_index()
        share = len([d for d in chosen if date(2008, 1, 2) <= d <= date(2026, 10, 16)]) / len(
            [d for d in days if date(2008, 1, 2) <= d <= date(2026, 10, 16)])  # fmt: skip
        print(f"  {name}: {per.min()} to {per.max()} a year, {share:.1%} of sessions")
    for name in ("fomc", "employment", "cpi"):
        inside = len(ann[name] & tom)
        print(f"  {name} days from 2008: {len(ann[name])}, of which inside a turn of the month: {inside}")


if __name__ == "__main__":
    fomc_announcements()
    for rid in (EMPLOYMENT, CPI):
        release_dates(rid)
    checks()
