"""
data.py — R7's data and its checks: every nominal Treasury note and bond auction from
2005 to 2026-10 from Treasury's two keyless sources, when each was announced, and the
bond ETFs (TLT, IEI, SHY and BIL) through research/r5_bond_carry/data.py. Phase 1 of
the study looks at these checks only: no rule's return, and no ETF return on or
around any auction date.

    python data.py           # fetch what isn't cached yet, then print every check
    python data.py --fetch   # fetch only

Sources, all free and keyless, cached in research/_data/treasury_auctions/
(git-ignored):
  * Fiscal Data's "Treasury Securities Auctions Data"
    (api.fiscaldata.treasury.gov, v1/accounting/od/auctions_query): one row an
    auction, with its announcement, auction and issue dates, its original term,
    whether it reopens an issue, the offering amount and the results (bid-to-cover,
    the indirect bidders' award).
  * TreasuryDirect's auction data (www.treasurydirect.gov/TA_WS/securities/search),
    one file a year and type: a second copy of the same auctions. Its auction date is
    the day competitive bids closed, and it gives the closing time.
  * The ETF master and the vendor files, through R5's data.py: the four ETFs'
    prices, ranges and volumes for the costs, and their volatility before the first
    decision (2005 to 2007) for the arithmetic in HYPOTHESIS.md.
Downloads are data only: parsed, never run.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[1]  # trading-engine
CACHE = HERE.parent / "_data" / "treasury_auctions"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))  # for the NYSE calendar (src/common/clock.py)

_spec = importlib.util.spec_from_file_location("r5_data", HERE.parent / "r5_bond_carry" / "data.py")
r5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r5)

FISCAL_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
    "?fields={fields}&filter=security_type:in:(Note,Bond),auction_date:gte:{start},auction_date:lte:{end}"
    "&sort=auction_date&page%5Bsize%5D=10000&page%5Bnumber%5D={page}"
)
FISCAL_FIELDS = (
    "cusip", "security_type", "security_term", "original_security_term", "reopening", "announcemt_date",
    "auction_date", "issue_date", "maturity_date", "offering_amt", "total_accepted", "bid_to_cover_ratio",
    "indirect_bidder_accepted", "high_yield", "inflation_index_security", "floating_rate",
)
DIRECT_URL = ("https://www.treasurydirect.gov/TA_WS/securities/search?format=json&securityType={kind}"
              "&startDate={year}-01-01&endDate={year}-12-31")  # fmt: skip
START, END = "2005-01-01", "2026-10-22"
YEARS = range(2005, 2027)

FIRST_DECISION = r5.FIRST_DECISION  # 2008-01-02
HOLDOUT_START = r5.HOLDOUT_START  # 2023-10-12, the last 756 sessions of the panel ending 2026-10-16
PANEL_END = r5.PANEL_END
PRE_WINDOW = (pd.Timestamp("2005-01-03"), pd.Timestamp("2007-12-31"))  # before the first decision

# Each ETF and the auctions of new supply in or at the edge of its maturity range.
# SHY's index holds 1 to 3 years left, IEI's 3 to 7, TLT's 20 or more: a new 3-year
# note sits on SHY's and IEI's boundary, and a new 20-year bond just under TLT's
# 20 years (check_index_edges).
TENORS = {"IEI": (3, 5, 7), "SHY": (2, 3), "TLT": (20, 30)}
WINDOW = 5  # sessions after an auction: Lou, Yan and Zhang's 5-day post-auction window
MIN_OFFERING_BN = 1.0  # smaller is no supply shock: two $25 million special reopenings (2019-06-21, 2021-12-02)
MONTH_END_DAYS = 3  # a session within this many of a month's last (either side): Etula et al.'s turn of the month

# Lou, Yan and Zhang (2013, RFS; NBER-era working paper of 2011), 1980 to June 2008:
# the 5-day return before an auction less the 5-day return after, on-the-run notes,
# in basis points of price (their introduction).
LYZ_DIFF_BPS = {2: 8.89, 5: 22.54, 10: 23.84}
# Fleming, Nguyen and Rosenberg (2024, JFE), Table 8, 1990-07 to 2020-12, yields
# adjusted for the day of the month: day 5's yield less the auction day's, in basis
# points (negative: the price rose after the auction).
FNR_POST5_BPS = {2: -2.00, 5: -2.57, 10: -0.01}


# ── fetching ─────────────────────────────────────────────────────────────


def fiscal_path(page: int = 1) -> Path:
    return CACHE / f"fiscaldata_auctions_{START}_{END}_p{page}.json"


def fetch() -> None:
    url = FISCAL_URL.format(fields=",".join(FISCAL_FIELDS), start=START, end=END, page=1)
    first = json.loads(r5._get(url, fiscal_path(1)).read_text())
    for page in range(2, int(first["meta"]["total-pages"]) + 1):
        r5._get(FISCAL_URL.format(fields=",".join(FISCAL_FIELDS), start=START, end=END, page=page), fiscal_path(page))
    for year in YEARS:
        for kind in ("Note", "Bond"):
            r5._get(DIRECT_URL.format(kind=kind, year=year), CACHE / "treasurydirect" / f"{kind}_{year}.json")


# ── the auctions ─────────────────────────────────────────────────────────


def _years(term: str) -> float:
    """'10-Year' -> 10, '29-Year 10-Month' -> 29.83, '' -> nan."""
    term = str(term or "")
    years = months = 0.0
    for part in term.split():
        if part.endswith("-Year"):
            years = float(part.split("-")[0])
        elif part.endswith("-Month"):
            months = float(part.split("-")[0])
    return years + months / 12 if term else math.nan


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _date(x) -> pd.Timestamp:
    return pd.Timestamp(str(x)[:10]) if x not in (None, "", "null") else pd.NaT


def fiscal() -> pd.DataFrame:
    """Fiscal Data's rows: every note and bond auction from 2005."""
    rows, page = [], 1
    while fiscal_path(page).exists():
        rows += json.loads(fiscal_path(page).read_text())["data"]
        page += 1
    out = pd.DataFrame({
        "cusip": [r["cusip"] for r in rows],
        "kind": [r["security_type"] for r in rows],
        "term": [_years(r["original_security_term"] or r["security_term"]) for r in rows],
        "term_left": [_years(r["security_term"]) for r in rows],
        "reopening": [r["reopening"] == "Yes" for r in rows],
        "announced": [_date(r["announcemt_date"]) for r in rows],
        "auction": [_date(r["auction_date"]) for r in rows],
        "issue": [_date(r["issue_date"]) for r in rows],
        "maturity": [_date(r["maturity_date"]) for r in rows],
        "offering_bn": [_num(r["offering_amt"]) / 1e9 for r in rows],
        "accepted_bn": [_num(r["total_accepted"]) / 1e9 for r in rows],
        "bid_to_cover": [_num(r["bid_to_cover_ratio"]) for r in rows],
        "indirect_bn": [_num(r["indirect_bidder_accepted"]) / 1e9 for r in rows],
        "high_yield": [_num(r["high_yield"]) for r in rows],
        "tips": [r["inflation_index_security"] == "Yes" for r in rows],
        "frn": [r["floating_rate"] == "Yes" for r in rows],
    })  # fmt: skip
    return out.sort_values(["auction", "cusip"]).reset_index(drop=True)


def direct() -> pd.DataFrame:
    """TreasuryDirect's rows: the same auctions, a second copy."""
    rows = []
    for year in YEARS:
        for kind in ("Note", "Bond"):
            path = CACHE / "treasurydirect" / f"{kind}_{year}.json"
            if path.exists():
                rows += json.loads(path.read_text())
    out = pd.DataFrame({
        "cusip": [r["cusip"] for r in rows],
        "kind": [r["securityType"] for r in rows],
        "term": [_years(r.get("originalSecurityTerm") or r["securityTerm"]) for r in rows],
        "reopening": [r["reopening"] == "Yes" for r in rows],
        "announced": [_date(r["announcementDate"]) for r in rows],
        "auction": [_date(r.get("auctionDate") or r["competitiveAuctionCloseDate"]) for r in rows],
        "closes_at": [r.get("competitiveAuctionCloseTime", "") for r in rows],
        "issue": [_date(r["issueDate"]) for r in rows],
        "offering_bn": [_num(r["offeringAmount"]) / 1e9 for r in rows],
        "bid_to_cover": [_num(r["bidToCoverRatio"]) for r in rows],
        "indirect_bn": [_num(r["indirectBiddersCompetitiveParAwarded"]) / 1e9 for r in rows],
        "high_yield": [_num(r["highYield"]) for r in rows],
        "tips": [r["inflationProtected"] == "Yes" for r in rows],
        "frn": [r["floatingRate"] == "Yes" for r in rows],
    })  # fmt: skip
    out = out[(out["auction"] >= START) & (out["auction"] <= END)]
    return out.sort_values(["auction", "cusip"]).drop_duplicates(["cusip", "auction"]).reset_index(drop=True)


def nominal(df: pd.DataFrame) -> pd.DataFrame:
    """Fixed-coupon notes and bonds: no TIPS, no floating-rate notes. The ETFs hold only
    these. The 30-year bonds first issued in 2007 and 2008 as 29 years 9 months count
    as 30-year."""
    out = df[~df["tips"] & ~df["frn"]].reset_index(drop=True)
    out["term"] = out["term"].where(out["term"] < 25, 30.0)
    return out


def sessions() -> pd.DatetimeIndex:
    return r5.sessions()


def calendar(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each nominal auction placed on the NYSE sessions: its session's position, the
    session before it (when the rule decides) and whether it was announced by then."""
    df = nominal(fiscal()) if df is None else df
    days = sessions()
    pos = days.get_indexer(df["auction"])
    out = df.assign(session=pos)
    out = out[out["session"] >= 0].copy()
    out["decided"] = days[out["session"] - 1]
    out["known_at_decision"] = out["announced"] <= out["decided"]
    out["lead_sessions"] = out["session"] - days.searchsorted(out["announced"])
    from src.common import clock

    out["early_close"] = [clock.is_early_close(t.date()) for t in out["auction"]]
    # the rules' auctions: announced by the decision's close, a real supply shock, and a
    # day the 15:35 run can buy in the closing auction
    out["used"] = out["known_at_decision"] & (out["offering_bn"] >= MIN_OFFERING_BN) & ~out["early_close"]
    return out


def windows(etf: str, *, n: int = WINDOW, cal: pd.DataFrame | None = None) -> pd.DataFrame:
    """The sessions a rule earns in ``etf``: the n sessions after each of its auctions
    (the close-to-close returns of sessions a+1 to a+n, bought in the auction day's
    closing auction), runs that overlap merged. One row a run."""
    cal = calendar() if cal is None else cal
    days = sessions()
    from src.common import clock

    early = np.array([clock.is_early_close(t.date()) for t in days])
    auctions = np.sort(cal.loc[cal["term"].isin(TENORS[etf]) & cal["used"], "session"].unique())
    runs, start, end = [], None, None
    for a in auctions:
        lo, hi = a + 1, min(a + n, len(days) - 1)
        while hi < len(days) - 1 and early[hi]:
            hi += 1  # the run can't sell in a 13:00 close: it holds to the next session
        if start is not None and lo <= end + 1:
            end = max(end, hi)
            continue
        if start is not None:
            runs.append((start, end))
        start, end = lo, hi
    if start is not None:
        runs.append((start, end))
    return pd.DataFrame({"first": [days[s] for s, _ in runs], "last": [days[e] for _, e in runs],
                         "sessions": [e - s + 1 for s, e in runs]})  # fmt: skip


def in_etf(etf: str, *, n: int = WINDOW) -> pd.Series:
    """True on each session whose return the rule earns in its ETF."""
    days = sessions()
    flag = pd.Series(False, index=days)
    for _, w in windows(etf, n=n).iterrows():
        flag.loc[w["first"]:w["last"]] = True
    return flag


# ── the checks ───────────────────────────────────────────────────────────


def check_coverage() -> list[str]:
    f, d = fiscal(), direct()
    out = [f"Fiscal Data: {len(f)} note and bond auctions from {f['auction'].min().date()} to {f['auction'].max().date()} "
           f"({int(f['tips'].sum())} TIPS, {int(f['frn'].sum())} floating-rate); TreasuryDirect: {len(d)}"]  # fmt: skip
    nom = nominal(f)
    out.append(f"nominal fixed-coupon auctions (the ETFs' securities): {len(nom)}")
    for term, g in nom.groupby("term"):
        new, reo = int((~g["reopening"]).sum()), int(g["reopening"].sum())
        out.append(f"  {term:g}-year: {len(g)} ({new} new issues, {reo} reopenings), {g['auction'].min().date()} to "
                   f"{g['auction'].max().date()}; per year from 2008: "
                   + ", ".join(f"{y} {n}" for y, n in g[g['auction'] >= '2008'].groupby(g['auction'].dt.year).size().items()))  # fmt: skip
    pending = nom[nom["bid_to_cover"].isna()]
    out.append(f"  with no results yet (announced, not yet held or just held): {len(pending)}: "
               + ", ".join(f"{r.auction.date()} {r.term:g}y" for r in pending.itertuples()))
    return out


def check_second_source() -> list[str]:
    f, d = nominal(fiscal()), nominal(direct())
    m = f.merge(d, on=["cusip", "auction"], how="outer", suffixes=("_f", "_d"), indicator=True)
    only_f, only_d = m[m["_merge"] == "left_only"], m[m["_merge"] == "right_only"]
    both = m[m["_merge"] == "both"]
    out = [f"the two sources, nominal auctions matched on CUSIP and auction date: {len(both)} in both, "
           f"{len(only_f)} only in Fiscal Data, {len(only_d)} only in TreasuryDirect"]  # fmt: skip
    for r in list(only_f.itertuples())[:6] + list(only_d.itertuples())[:6]:
        out.append(f"  only in one: {r.auction.date()} {r.cusip}")
    for col, tol in (("announced", None), ("issue", None), ("term", 1e-9), ("reopening", None),
                     ("offering_bn", 1e-9), ("bid_to_cover", 1e-6), ("high_yield", 1e-6), ("indirect_bn", 1e-6)):
        a, b = both[f"{col}_f"], both[f"{col}_d"]
        if tol is None:
            bad = both[(a != b) & ~(a.isna() & b.isna())]
        else:
            bad = both[((a - b).abs() > tol) & ~(a.isna() & b.isna())]
        out.append(f"  {col}: {len(both) - len(bad)} of {len(both)} agree"
                   + ("" if not len(bad) else "; differ on " + ", ".join(
                       f"{r.auction.date()} {r.cusip} ({getattr(r, col + '_f')} / {getattr(r, col + '_d')})"
                       for r in list(bad.itertuples())[:4])))  # fmt: skip
    return out


def check_point_in_time() -> list[str]:
    cal = calendar()
    days = sessions()
    inside = nominal(fiscal())
    inside = inside[inside["auction"] <= days[-1]]
    closes = nominal(direct())["closes_at"].replace("", "not given").value_counts()
    out_close = "competitive bids closed at: " + ", ".join(f"{k} ({v})" for k, v in closes.items())
    out = [f"nominal auctions to the panel's last session ({days[-1].date()}) on NYSE sessions: {len(cal)} of "
           f"{len(inside)}; on other days: " + (", ".join(str(t.date()) for t in inside["auction"] if t not in days) or "none")]
    lead = cal["lead_sessions"]
    out.append(f"sessions from the announcement to the auction: median {lead.median():.0f}, 5th percentile "
               f"{lead.quantile(0.05):.0f}, fewest {lead.min():.0f}, most {lead.max():.0f}")  # fmt: skip
    for k, n in lead.value_counts().sort_index().items():
        out.append(f"  {k} sessions: {n}")
    late = cal[~cal["known_at_decision"]]
    out.append(f"auctions announced after the close of the session before them (the rule's decision): {len(late)}"
               + ("" if not len(late) else ": " + ", ".join(f"{r.auction.date()} {r.term:g}y" for r in late.itertuples())))

    early = cal[cal["early_close"]]
    out.append(f"auctions on NYSE early-close days (the market closes at 13:00, before the 15:35 run): {len(early)}"
               + ("" if not len(early) else ": " + ", ".join(f"{r.auction.date()} {r.term:g}y" for r in early.itertuples())))
    small = cal[cal["offering_bn"] < MIN_OFFERING_BN]
    out.append(f"auctions under ${MIN_OFFERING_BN:g}bn, special reopenings and no supply shock: {len(small)}: "
               + ", ".join(f"{r.auction.date()} {r.term:g}y ${r.offering_bn * 1e3:.0f}M" for r in small.itertuples()))
    out.append(out_close + "; all before the 16:00 closing auction")
    out.append(f"so the rules use {int(cal['used'].sum())} of the {len(cal)} auctions: each known by the close before it, "
               f"at least ${MIN_OFFERING_BN:g}bn, and not on an early-close day")  # fmt: skip
    return out


def check_index_edges() -> list[str]:
    """Where a new issue sits against each ETF's index range at its first month-end."""
    nom = nominal(fiscal())
    new = nom[~nom["reopening"]].copy()
    month_end = (new["issue"] + pd.offsets.MonthEnd(0))
    left = (new["maturity"] - month_end).dt.days / 365.25
    out = ["each new issue's years to maturity at the month-end after it settles (the indices rebalance then):"]
    for term in (2, 3, 5, 7, 20, 30):
        x = left[new["term"] == term]
        if len(x):
            out.append(f"  {term}-year: {x.min():.2f} to {x.max():.2f}")
    out.append("  so a new 3-year note enters SHY's 1-3 year range, not IEI's 3-7; a new 20-year bond is under TLT's 20+; "
               "the rules take them as supply at the range's edge (HYPOTHESIS.md)")
    return out


def check_calendar() -> list[str]:
    cal = calendar()
    from_end = month_edges(sessions())["to_end"]
    out = ["each term's auctions from 2008, by how many sessions before its month's last session it fell (0 = the last):"]
    for term, g in cal[cal["auction"] >= FIRST_DECISION].groupby("term"):
        fe = from_end.reindex(g["auction"]).to_numpy()
        out.append(f"  {term:g}-year: median {np.median(fe):.0f}, from {fe.min():.0f} to {fe.max():.0f}; in the month's last "
                   f"{MONTH_END_DAYS} sessions: {int((fe < MONTH_END_DAYS).sum())} of {len(fe)}")  # fmt: skip
    return out


def month_edges(days: pd.DatetimeIndex) -> pd.DataFrame:
    """Each session's distance in sessions to its month's last session (0 = the last)
    and from its month's first (0 = the first)."""
    s = pd.Series(np.arange(len(days)), index=days)
    g = s.groupby([days.year, days.month])
    return pd.DataFrame({"to_end": g.transform("max") - s, "from_start": s - g.transform("min")}, index=days)


def turn_of_month(days: pd.DatetimeIndex) -> pd.Series:
    """The last MONTH_END_DAYS sessions of each month and the first MONTH_END_DAYS of
    the next: where Etula et al.'s month-end pattern sits."""
    e = month_edges(days)
    return (e["to_end"] < MONTH_END_DAYS) | (e["from_start"] < MONTH_END_DAYS)


def check_windows() -> list[str]:
    days = sessions()
    wf = days[(days > FIRST_DECISION) & (days < HOLDOUT_START)]
    ho = days[(days >= HOLDOUT_START) & (days <= PANEL_END)]
    near_end = turn_of_month(days)
    out = [f"what each rule would hold, from the calendar alone ({WINDOW} sessions after each auction of its terms):"]
    for etf in TENORS:
        flag = in_etf(etf)
        runs = windows(etf)
        runs_wf = runs[(runs["first"] > FIRST_DECISION) & (runs["first"] < HOLDOUT_START)]
        runs_ho = runs[runs["first"] >= HOLDOUT_START]
        years_wf = len(wf) / 252
        out.append(f"  {etf} ({', '.join(f'{t}-year' for t in TENORS[etf])}): in the ETF on {int(flag.reindex(wf).sum())} of "
                   f"{len(wf)} walk-forward sessions ({flag.reindex(wf).mean():.0%}) and {int(flag.reindex(ho).sum())} of "
                   f"{len(ho)} holdout sessions ({flag.reindex(ho).mean():.0%}); {len(runs_wf)} runs in the walk-forward "
                   f"({len(runs_wf) / years_wf:.1f} a year), {len(runs_ho)} in the holdout; run lengths "
                   + ", ".join(f"{k} sessions x{v}" for k, v in runs["sessions"].value_counts().sort_index().items()))  # fmt: skip
        yearly = flag.reindex(wf).groupby(wf.year).sum()
        out.append("    sessions in the ETF by year: " + ", ".join(f"{y} {int(v)}" for y, v in yearly.items()))
        held = flag.reindex(wf)
        out.append(f"    of its walk-forward sessions in the ETF, in the last or first {MONTH_END_DAYS} of a month: "
                   f"{near_end.reindex(held[held].index).mean():.0%} (of all sessions: {near_end.reindex(wf).mean():.0%})")
    return out


def check_costs() -> list[str]:
    out = ["costs: each ETF's prices and volumes from 2008, the commission and one cent of spread in bps"]
    for s in ("IEI", "SHY", "TLT", "BIL"):
        m = r5.master(s).loc[FIRST_DECISION:PANEL_END]
        px = m["close"]
        adv = (m["close"] * m["volume"]).groupby(m.index.year).median() / 1e6
        out.append(f"  {s}: ${px.min():.2f} to ${px.max():.2f}; $0.005 a share is {50 / px.max():.2f} to {50 / px.min():.2f} "
                   f"bps; one cent {100 / px.max():.2f} to {100 / px.min():.2f} bps; median daily dollar volume "
                   f"${adv.min():,.0f}M to ${adv.max():,.0f}M a year")  # fmt: skip
    return out


def pre_window_volatility() -> dict[str, float]:
    """Each ETF's daily volatility, annualized, before the first decision (2005 to 2007;
    IEI from 2007-01-11): an unconditional figure for HYPOTHESIS.md's arithmetic."""
    out = {}
    for s in ("IEI", "SHY", "TLT"):
        tr = r5.tr_closes(s).loc[PRE_WINDOW[0]:PRE_WINDOW[1]]
        out[s] = float(tr.pct_change().dropna().std() * math.sqrt(252))
    return out


NOTE_DURATION = {2: 1.9, 5: 4.6, 10: 8.0}  # on-the-run notes' approximate durations, to turn LYZ's price into yield
PROXY = {"IEI": 5, "SHY": 2, "TLT": 10}  # the published tenor nearest each ETF's auctions (none covers the 20- or 30-year)
HALF_SPREAD = {"IEI": 2.0, "SHY": 1.0, "TLT": 1.0, "BIL": 1.0}  # bps, R5's
COMMISSION = {"IEI": 0.43, "SHY": 0.60, "TLT": 0.45, "BIL": 0.55}  # bps: $0.005 a share at each ETF's prices (R5)
HELD_SHARPE = {"IEI": 0.338, "SHY": 0.259, "TLT": 0.187}  # each ETF held, 2008-2023 walk-forward, R5's report


IMPACT_K = 0.5  # S1's CostModel: impact = k x daily volatility x sqrt(trade / daily dollar volume)
BOOK = 1e6  # dollars: the harness's book starts at $1M, and a run trades all of it


def run_cost(etf: str) -> float:
    """Basis points of NAV a run costs before impact: BIL sold and the ETF bought, then back."""
    return 2 * (HALF_SPREAD[etf] + COMMISSION[etf] + HALF_SPREAD["BIL"] + COMMISSION["BIL"])


def impact_per_run(etf: str, daily_vol: float) -> float:
    """The square-root impact of a run's two ETF trades, in bps of NAV, averaged over the
    walk-forward's years at each year's median dollar volume (BIL's is negligible: its
    daily volatility is about a hundredth of a percent)."""
    m = r5.master(etf).loc[FIRST_DECISION:HOLDOUT_START]
    adv = (m["close"] * m["volume"]).groupby(m.index.year).median()
    per_trade = IMPACT_K * daily_vol * np.sqrt(BOOK / adv) * 1e4
    return float(2 * per_trade.mean())


def check_arithmetic() -> list[str]:
    """The literature's effect in each ETF's price, a run's cost, and the Sharpe ratio
    they allow: ex-ante arithmetic from published figures, the calendar, each ETF's
    volatility before 2008 and R5's reference books. No ETF return around an auction
    is computed."""
    vol = pre_window_volatility()
    days = sessions()
    wf = days[(days > FIRST_DECISION) & (days < HOLDOUT_START)]
    years = len(wf) / 252
    out = ["the literature in each ETF's price, at its 2026-10-20 duration (issuer), volatility 2005-07 ("
           + ", ".join(f"{k} {v:.1%}" for k, v in vol.items()) + "):"]
    for etf in TENORS:
        runs = windows(etf)
        n_runs = len(runs[(runs["first"] > FIRST_DECISION) & (runs["first"] < HOLDOUT_START)]) / years
        frac = float(in_etf(etf).reindex(wf).mean())
        sigma = vol[etf] * math.sqrt(frac)  # the book's volatility: in the ETF that share of sessions, else BIL
        base = HELD_SHARPE[etf] * math.sqrt(frac)  # the ETF held at random times as often: its own premium
        impact = impact_per_run(etf, vol[etf] / math.sqrt(252))
        cost = run_cost(etf) + impact
        tenor = PROXY[etf]
        d = r5.ISSUER[etf]["duration"]
        cases = {
            f"LYZ 1980-2008, half the {tenor}-year's pre/post difference": LYZ_DIFF_BPS[tenor] / 2 / NOTE_DURATION[tenor],
            f"FNR 1990-2020, the {tenor}-year's day +5": -FNR_POST5_BPS[tenor],
        }
        out.append(f"  {etf}: {n_runs:.1f} runs a year in the walk-forward, in the ETF on {frac:.0%} of sessions; a run costs "
                   f"{cost:.1f} bps of NAV ({impact:.1f} of it impact on a $1M book); held as often at random it would "
                   f"score about {base:.2f}")  # fmt: skip
        for label, y in cases.items():
            gain = y * d  # bps of price a run
            gross, net = gain * n_runs / 1e4, (gain - cost) * n_runs / 1e4
            out.append(f"    {label}: {y:.2f} bps of yield, {gain:.1f} bps of {etf}'s price a run, {gross:.2%} a year "
                       f"gross and {net:.2%} net: a Sharpe ratio of about {base + gross / sigma:.2f} gross and "
                       f"{base + net / sigma:.2f} net, before any fall since publication")  # fmt: skip
    return out


def check_borrow() -> list[str]:
    return ["borrow fees: no free source keeps their history (IBKR's file of shortable shares and fees, on its "
            "public FTP, is today's only; R6 found iBorrowDesk refusing connections). So no rule here is short."]


CHECKS = (check_coverage, check_second_source, check_point_in_time, check_index_edges, check_calendar,
          check_windows, check_costs, check_arithmetic, check_borrow)  # fmt: skip


def checks() -> None:
    for check in CHECKS:
        for line in check():
            print(line)
        print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fetch", action="store_true", help="fetch only")
    args = p.parse_args(argv)
    fetch()
    if not args.fetch:
        checks()
    return 0


if __name__ == "__main__":
    sys.exit(main())
