"""
r2_vol_positioning.py — R2 through the research gate: H1 (the VIX term structure as risk
timing for SPY), H1b (the same signal as an overlay on S0) and H2 (COT hedging pressure on
ETF proxies). HYPOTHESIS.md has the rules, split and criteria, and the deviations fixed
before any run; REPORT.md the verdict.

    python r2_vol_positioning.py                      # the walk-forward, then the holdout: every number
    python r2_vol_positioning.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python r2_vol_positioning.py --stage holdout      # the holdout

Use the repo's .venv Python. Reuses research/s1_trend's data, costs, harness and measures and
research/s3a_vol_overlay's S0 books and bootstrap, so the numbers compare with both. Reads the
Cboe and CFTC files data.py cached in research/_data/ (it downloads them only if they're
missing) and writes results/.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s1_trend"))
sys.path.insert(0, str(HERE.parent / "s3a_vol_overlay"))
import data  # noqa: E402
import s1_trend as s1t  # noqa: E402
import s3a_vol_overlay as s3a  # noqa: E402
from dv02_engine.walkforward import walk_forward  # noqa: E402

RESULTS = HERE / "results"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
H1_FIRST_DECISION = "2009-10-01"  # VIX3M's free history starts on 2009-09-18
H2_FIRST_DECISION = s1t.FIRST_DECISION  # 2008-01-02, as S1's
H1_VARIANTS = [(1.00, 1), (0.95, 1), (1.00, 5), (0.95, 5)]  # (threshold, closes averaged)
H1_PRIMARY = (1.00, 1)
H1B_THRESHOLD = 1.00
STRESS = {
    "2011 (Jul 22 to Oct 3)": ("2011-07-22", "2011-10-03"),
    "2015-16 (Aug 17 to Feb 11)": ("2015-08-17", "2016-02-11"),
    "2018 (Sep 20 to Dec 24)": ("2018-09-20", "2018-12-24"),
    "2020 crash (Feb 19 to Mar 23)": ("2020-02-19", "2020-03-23"),
    "2022": ("2022-01-01", "2022-12-31"),
}
# the ETF proxies and their CFTC codes (legacy futures-only), in HYPOTHESIS.md's order
H2_MARKETS = {
    "GLD": "088691", "SLV": "084691", "USO": "067651", "UNG": "023651", "TLT": "020601",
    "IEI": "044601", "SPY": "13874A", "QQQ": "209742", "VEA": "244041", "VWO": "244042",
}  # fmt: skip
H2_VARIANTS = ("TS-156", "TS-52", "XS-level", "Change-4w")
H2_PRIMARY = "TS-156"
COT_HISTORY = 156  # reports an asset needs, usable by the decision, to be traded
REPORT_LAG = pd.Timedelta(days=7)  # a report is used from the first close 7 days after its as-of date
RELEASE_MARGIN = pd.Timedelta(days=4)  # and, for one published late, 4 days after its release
# Reports the CFTC published late, as-of date: release date (HYPOTHESIS.md, deviations).
LATE_RELEASES = {
    # 2013 shutdown, press release 6745-13; a release "during the week of" taken as its Friday
    "2013-10-01": "2013-10-25", "2013-10-08": "2013-11-01", "2013-10-15": "2013-11-01",
    "2013-10-22": "2013-11-08", "2013-10-29": "2013-11-08",
    # 2018-19 shutdown, press release 7864-19: Feb 1, then one each Tuesday and Friday in order
    "2018-12-24": "2019-02-01", "2018-12-31": "2019-02-05", "2019-01-08": "2019-02-08",
    "2019-01-15": "2019-02-12", "2019-01-22": "2019-02-15", "2019-01-29": "2019-02-19",
    "2019-02-05": "2019-02-22", "2019-02-12": "2019-02-26", "2019-02-19": "2019-03-01",
    "2019-02-26": "2019-03-05",
    # 2023, the ION Markets incident: the COT page's special announcements
    "2023-01-31": "2023-02-24", "2023-02-07": "2023-03-03", "2023-02-14": "2023-03-08",
    "2023-02-21": "2023-03-10", "2023-02-28": "2023-03-14", "2023-03-07": "2023-03-16",
    "2023-03-14": "2023-03-21",
    # 2025 shutdown: the CFTC's schedule of 2025-12-09
    "2025-09-30": "2025-11-19", "2025-10-07": "2025-11-21", "2025-10-14": "2025-11-25",
    "2025-10-21": "2025-12-02", "2025-10-28": "2025-12-05", "2025-11-04": "2025-12-09",
    "2025-11-10": "2025-12-10", "2025-11-18": "2025-12-12", "2025-11-25": "2025-12-15",
    "2025-12-02": "2025-12-17", "2025-12-09": "2025-12-19", "2025-12-16": "2025-12-23",
}  # fmt: skip
N_TRIALS = 23  # S1's 8 + S3a's 6 + H1's 4 + H1b's 1 + H2's 4
S1_TRIALS = HERE.parent / "s1_trend" / "results" / "walkforward_variants.csv"
S3A_TRIALS = HERE.parent / "s3a_vol_overlay" / "results" / "walkforward_books.csv"
BLOCK, RESAMPLES, SEED = s3a.BLOCK, s3a.RESAMPLES, s3a.SEED
SHARPE_SLACK = s3a.SHARPE_SLACK  # H1b's criteria 2 and 4, as S3a's

H1_NAMES = {v: f"H1 {v[0]:.2f} {v[1]}d" for v in H1_VARIANTS}
H1_NAME = H1_NAMES[H1_PRIMARY]
H1B_NAME = "H1b S0 out while inverted"
H2_NAMES = {v: f"H2 {v}" for v in H2_VARIANTS}
H2_NAME = H2_NAMES[H2_PRIMARY]
H2_CHECK = f"{H2_NAME}, 7-day rule for every report (check)"
H2_MIX = f"50/50 S0 + {H2_NAME}"
SPY_HELD, S0_NAME, S1_NAME = "SPY held", "S0", "S1 blend long/flat"


# ── data ──────────────────────────────────────────────────────────────


def _cached() -> None:
    """Download what data.py hasn't cached yet; nothing is refetched otherwise."""
    if not all((data.CBOE_DIR / f"{n}_History.csv").exists() for n in ("VIX", "VIX3M")):
        data.fetch_cboe()
    if not any(data.CFTC_DIR.glob("deacot*/*.txt")):
        data.fetch_cftc()


def term_structure() -> pd.DataFrame:
    """Cboe's VIX and VIX3M closes on the days both have one, and their ratio."""
    both = pd.concat({"VIX": data.cboe("VIX"), "VIX3M": data.cboe("VIX3M")}, axis=1).dropna()
    both["ratio"] = both["VIX"] / both["VIX3M"]
    return both


def available_from(as_of: pd.Timestamp, *, late: bool = True) -> pd.Timestamp:
    """The first close a report may be used at: 7 days after its as-of date and, for one the
    CFTC published late (with late=True), 4 days after its release."""
    when = as_of + REPORT_LAG
    release = LATE_RELEASES.get(as_of.strftime("%Y-%m-%d")) if late else None
    return max(when, pd.Timestamp(release) + RELEASE_MARGIN) if release else when


def cot_reports(*, late: bool = True) -> dict[str, pd.DataFrame]:
    """Per H2 ETF, its futures market's reports by as-of date: hedging pressure (commercial
    short - long over their sum), commercial net short as a share of open interest, and the
    first close each may be used at."""
    cot = data.cot()
    out = {}
    for etf, code in H2_MARKETS.items():
        rows = cot[cot["code"] == code].sort_values("as_of")
        long, short = rows["comm_long"].astype(float), rows["comm_short"].astype(float)
        frame = pd.DataFrame({
            "as_of": rows["as_of"].to_numpy(),
            "hp": ((short - long) / (short + long)).to_numpy(),
            "net_short_share": ((short - long) / rows["open_interest"].astype(float)).to_numpy(),
        })  # fmt: skip
        frame["available"] = [available_from(pd.Timestamp(a), late=late) for a in frame["as_of"]]
        out[etf] = frame.dropna(subset=["hp", "net_short_share"]).reset_index(drop=True)
    return out


# ── strategies ────────────────────────────────────────────────────────


def ratio_at(ratio: pd.Series, t: pd.Timestamp, closes: int) -> float:
    """VIX / VIX3M at the close of t: the latest Cboe close on or before t, or the mean of the
    last `closes` of them."""
    known = ratio.loc[:t]
    if len(known) < closes:
        raise ValueError(f"no VIX3M history for {t.date()}")
    return float(known.iloc[-closes:].mean())


def h1(threshold: float, closes: int, ratio: pd.Series):
    """H1: SPY at 99.5% while the ratio is below `threshold` (contango), else BIL at 99.5%.
    Decided every day; a day with no change holds."""
    state: dict[str, bool | None] = {"contango": None}

    def strategy(history: pd.DataFrame):
        contango = ratio_at(ratio, history.index[-1], closes) < threshold
        if contango == state["contango"]:
            return None
        state["contango"] = contango
        return {"SPY": BUDGET} if contango else {CASH: BUDGET}

    return strategy


def h1b(targets: pd.DataFrame, ratio: pd.Series):
    """H1b: S0's latest target, with the whole book moved to BIL while the curve is inverted
    (the day's ratio at or above 1.00). Trades on S0's dates and when the signal flips."""
    by_date = s3a.s0_books(targets)
    state: dict[str, object] = {"base": None, "contango": None}

    def strategy(history: pd.DataFrame):
        t = history.index[-1]
        fresh = by_date.get(t)
        if fresh is not None:
            state["base"] = fresh
        contango = ratio_at(ratio, t, 1) < H1B_THRESHOLD
        flipped = state["contango"] is not None and contango != state["contango"]
        state["contango"] = contango
        base = state["base"]
        if base is None or (fresh is None and not flipped):
            return None
        if contango:
            return {s: float(w) for s, w in base.items()}
        return {**{s: 0.0 for s in base.index}, CASH: float(base.sum())}

    return strategy


def spy_held():
    """SPY bought at 99.5% at the first trade and never rebalanced."""
    state = {"bought": False}

    def strategy(history: pd.DataFrame):
        if state["bought"]:
            return None
        state["bought"] = True
        return {"SPY": BUDGET}

    return strategy


def h2_signals(variant: str, usable: dict[str, pd.DataFrame]) -> dict[str, float]:
    """1 (long) or 0 (flat) per eligible asset, from its reports usable at the decision."""
    if variant == "XS-level":
        latest = {a: float(r["hp"].iloc[-1]) for a, r in usable.items()}
        ranked = sorted(latest, key=lambda a: -latest[a])  # stable: a tie keeps the table's order
        top = set(ranked[: math.ceil(len(ranked) / 2)])
        return {a: float(a in top) for a in usable}
    out = {}
    for a, r in usable.items():
        if variant in ("TS-156", "TS-52"):
            window = int(variant.split("-")[1])
            out[a] = float(r["hp"].iloc[-1] > r["hp"].iloc[-window:].median())
        elif variant == "Change-4w":
            out[a] = float(r["net_short_share"].iloc[-1] > r["net_short_share"].iloc[-5])
        else:
            raise ValueError(variant)
    return out


def h2_eligible(history: pd.DataFrame, reports: dict[str, pd.DataFrame]) -> tuple[list[str], dict[str, pd.DataFrame]]:
    """The assets H2 may trade at the close of history's last row (253 closes, S1's rule with
    UNG's late start, and 156 usable reports), and every asset's usable reports."""
    t = history.index[-1]
    tail = history.iloc[-s1t.HISTORY_ROWS :]
    usable = {a: r[r["available"] <= t] for a, r in reports.items()}
    eligible = [
        a for a in H2_MARKETS
        if a in tail.columns and tail[a].notna().all() and tail.index[0] >= s1t.FIRST_HISTORY.get(a, tail.index[0])
        and len(usable[a]) >= COT_HISTORY
    ]  # fmt: skip
    return eligible, usable


def h2(variant: str, reports: dict[str, pd.DataFrame]):
    """H2: each eligible asset long or flat by its hedging pressure, sized as S1's long/flat
    books (inverse 63-day vol to a 99.5% budget, 20% name cap, 10% vol ceiling that only
    scales down, BIL for the rest). Decided every 21 trading days."""

    def strategy(history: pd.DataFrame):
        tail = history.iloc[-s1t.HISTORY_ROWS :]
        if len(tail) < s1t.HISTORY_ROWS:
            return None
        eligible, usable = h2_eligible(history, reports)
        cash_ok = bool(np.isfinite(history[CASH].iloc[-1]))
        if not eligible:
            return {CASH: BUDGET} if cash_ok else None
        signal = h2_signals(variant, {a: usable[a] for a in eligible})
        # from here on, S1's sizing (research/s1_trend/s1_trend.py, s1), unchanged
        returns = tail[eligible].iloc[-(s1t.VOL_ROWS + 1) :].pct_change(fill_method=None).iloc[1:]
        sigma = returns.std(ddof=1) * math.sqrt(TRADING_DAYS)
        sigma = sigma[sigma > 0]
        if sigma.empty:
            return {CASH: BUDGET} if cash_ok else None
        inverse = 1.0 / sigma
        w = pd.Series({a: signal[a] * inverse[a] for a in sigma.index}) * (BUDGET / inverse.sum())
        w = w.clip(-s1t.NAME_CAP, s1t.NAME_CAP)
        cov = returns[sigma.index].cov().to_numpy() * TRADING_DAYS
        book_vol = math.sqrt(max(float(w.to_numpy() @ cov @ w.to_numpy()), 0.0))
        if book_vol > s1t.VOL_CEILING:
            w = w * (s1t.VOL_CEILING / book_vol)
        weights = {a: float(v) for a, v in w.items() if v != 0.0}
        if cash_ok:
            weights[CASH] = BUDGET - float(w.abs().sum())
        return weights

    return strategy


# ── running and measuring ─────────────────────────────────────────────


def run(panel, volumes, strategy, *, first_decision: str, rebalance_every: int, holdout: int):
    """S1's harness (costs, lag 1, the mandate check, yearly test blocks), from first_decision."""
    first = int(panel.index.searchsorted(pd.Timestamp(first_decision)))
    return walk_forward(panel, strategy, train=first, test=s1t.TEST_ROWS, costs=s1t.COSTS, n_trials=N_TRIALS,
                        holdout=holdout, rebalance_every=rebalance_every, lag=1, volumes=volumes,
                        mandate=True)  # fmt: skip


def h1_books(panel, volumes, ratio, targets, *, holdout: int, variants: bool = True) -> dict[str, object]:
    """Every book on H1's window, by name: walk-forward results."""
    books = {SPY_HELD: (spy_held(), 1), H1B_NAME: (h1b(targets, ratio), 1), S0_NAME: (s1t.s0(targets), 1),
             S1_NAME: (s1t.s1("blend", "long_flat"), s1t.REBALANCE_EVERY)}  # fmt: skip
    for v in H1_VARIANTS if variants else [H1_PRIMARY]:
        books[H1_NAMES[v]] = (h1(v[0], v[1], ratio), 1)
    return {name: run(panel, volumes, strategy, first_decision=H1_FIRST_DECISION, rebalance_every=every,
                      holdout=holdout) for name, (strategy, every) in books.items()}  # fmt: skip


def h2_books(panel, volumes, targets, reports, reports_7day, *, holdout: int, variants: bool = True):
    """Every book on H2's window, by name: walk-forward results."""
    books = {S0_NAME: (s1t.s0(targets), 1), S1_NAME: (s1t.s1("blend", "long_flat"), s1t.REBALANCE_EVERY),
             H2_CHECK: (h2(H2_PRIMARY, reports_7day), s1t.REBALANCE_EVERY)}  # fmt: skip
    for v in H2_VARIANTS if variants else [H2_PRIMARY]:
        books[H2_NAMES[v]] = (h2(v, reports), s1t.REBALANCE_EVERY)
    return {name: run(panel, volumes, strategy, first_decision=H2_FIRST_DECISION, rebalance_every=every,
                      holdout=holdout) for name, (strategy, every) in books.items()}  # fmt: skip


def on_common_days(results: dict[str, object]) -> dict[str, pd.Series]:
    """Each book's daily returns, on the days every book has."""
    daily = {name: wf.equity["daily_return"] for name, wf in results.items()}
    common = None
    for r in daily.values():
        common = r.index if common is None else common.intersection(r.index)
    return {name: r.loc[common] for name, r in daily.items()}


def paired_bootstrap(book: pd.Series, base: pd.Series, panel: pd.DataFrame, slack: float) -> dict[str, float]:
    """S3a's paired bootstrap (research/s3a_vol_overlay, paired_bootstrap): the same 63-day
    blocks drawn for the book and its benchmark, seed 21. Its Sharpe probability is read at
    `slack`: P(the book's excess Sharpe >= the benchmark's - slack)."""
    x_b = s1t.excess_returns(book, panel).to_numpy()
    x_0 = s1t.excess_returns(base, panel).to_numpy()
    r_b, r_0 = book.to_numpy(), base.to_numpy()
    n = len(r_b)
    rng = np.random.default_rng(SEED)
    blocks = math.ceil(n / BLOCK)
    d_sharpe, d_dd = np.empty(RESAMPLES), np.empty(RESAMPLES)
    for i in range(RESAMPLES):
        starts = rng.integers(0, n - BLOCK + 1, size=blocks)
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts])[:n]
        d_sharpe[i] = s3a._sharpe(x_b[idx]) - s3a._sharpe(x_0[idx])
        d_dd[i] = s3a._max_drawdown(r_b[idx]) - s3a._max_drawdown(r_0[idx])  # > 0: the book's is shallower
    return {
        "slack": slack,
        "p_drawdown_shallower": float((d_dd > 0).mean()),
        "p_sharpe_at_least_benchmark_less_slack": float((d_sharpe >= -slack).mean()),
        **{f"sharpe_diff_p{p}": float(np.percentile(d_sharpe, p)) for p in (5, 50, 95)},
        **{f"drawdown_diff_p{p}": float(np.percentile(d_dd, p)) for p in (5, 50, 95)},
    }


def spy_days(events: pd.DataFrame, index: pd.DatetimeIndex) -> float:
    """The share of days a book held SPY, from its trades."""
    if "SPY" not in events.columns:
        return 0.0
    held = pd.Series(events["SPY"].to_numpy(), index=pd.to_datetime(events["executed"]))
    held = held[~held.index.duplicated(keep="last")].reindex(index, method="ffill").fillna(0.0)
    return float((held > 0).mean())


def book_rows(results, daily, panel, trial_sharpes, references: dict[str, str]) -> pd.DataFrame:
    """One row per book: S1's measures, the harness's costs and mandate record, the deflated
    Sharpe over all N_TRIALS trials, exposure, and daily correlations with the references."""
    rows = []
    for name, r in daily.items():
        row = {"book": name, **s1t.measure(r, panel)}
        dsr, threshold = s1t.deflated(s1t.excess_returns(r, panel), N_TRIALS, trial_sharpes)
        if name in results:
            c, events = results[name].combined, results[name].events
            row.update(turnover=c["turnover"], cost_drag=c["cost_drag"], total_cost=c["total_cost"],
                       mandate_breach_days=c["mandate_breach_days"], max_gross_exposure=c["max_gross_exposure"],
                       trades=float(len(events)), trades_per_year=len(events) / row["years"],
                       share_of_days_in_spy=spy_days(events, r.index), **s3a.risky_exposure(events))  # fmt: skip
        row.update(deflated_sharpe=dsr, deflated_sharpe_threshold=threshold)
        for label, ref in references.items():
            row[f"correlation_with_{label}"] = float(r.corr(daily[ref]))
        rows.append(row)
    return pd.DataFrame(rows)


def mix_record(c0: dict, c1: dict) -> dict[str, float]:
    """A 50/50 book's turnover, cost drag and mandate record, as S3a's: half of each sleeve's."""
    return {
        "turnover": 0.5 * c0["turnover"] + 0.5 * c1["turnover"],
        "cost_drag": 0.5 * c0["cost_drag"] + 0.5 * c1["cost_drag"],
        "mandate_breach_days": c0["mandate_breach_days"] + c1["mandate_breach_days"],
    }


def trials(h1_daily, h2_daily, panel) -> pd.DataFrame:
    """The 23 trials' excess Sharpe ratios: S1's and S3a's from their results files, these nine
    from this walk-forward."""
    s1_rows = pd.read_csv(S1_TRIALS)
    s3a_rows = pd.read_csv(S3A_TRIALS).set_index("book").loc[s3a.TRIAL_BOOKS].reset_index()
    ours = [H1_NAMES[v] for v in H1_VARIANTS] + [H1B_NAME]
    out = pd.DataFrame(
        [{"study": "S1", "trial": r["variant"], "sharpe_excess": r["sharpe_excess"]} for _, r in s1_rows.iterrows()]
        + [{"study": "S3a", "trial": r["book"], "sharpe_excess": r["sharpe_excess"]} for _, r in s3a_rows.iterrows()]
        + [{"study": "R2", "trial": n, "sharpe_excess": s1t.measure(h1_daily[n], panel)["sharpe_excess"]} for n in ours]
        + [{"study": "R2", "trial": H2_NAMES[v], "sharpe_excess": s1t.measure(h2_daily[H2_NAMES[v]], panel)["sharpe_excess"]}
           for v in H2_VARIANTS]
    )  # fmt: skip
    assert len(out) == N_TRIALS, len(out)
    return out


def cboe_coverage(ts: pd.DataFrame, span: pd.DatetimeIndex) -> list[dict[str, str]]:
    """The sessions in span without a same-day VIX / VIX3M ratio, which use the latest before."""
    latest = ts["ratio"].reindex(span, method="ffill")
    stale = [(d, ts.index[ts.index <= d][-1]) for d in span if d not in ts.index]
    where = f"sessions {span[0].date()} to {span[-1].date()}"
    return [
        {"check": f"{where} with no same-day Cboe close (the latest before is used)",
         "value": f"{len(stale)}: " + "; ".join(f"{d.date()} uses {u.date()}" for d, u in stale)},
        {"check": f"{where} with no ratio at all", "value": str(int(latest.isna().sum()))},
    ]  # fmt: skip


def data_checks(panel, ts, reports, s0_2008: pd.Series) -> pd.DataFrame:
    """Coverage of the Cboe and CFTC data before the holdout, the late releases applied, and
    S0 against S3a's run."""
    rows = [{"check": "Cboe VIX and VIX3M, both", "value": f"from {ts.index[0].date()}"}]
    days = panel.index[: len(panel) - s1t.HOLDOUT_ROWS]
    rows += cboe_coverage(ts, days[days >= pd.Timestamp(H1_FIRST_DECISION)])
    late = {pd.Timestamp(a) for a in LATE_RELEASES}
    for etf, r in reports.items():
        eligible = r["available"].iloc[COT_HISTORY - 1] if len(r) >= COT_HISTORY else None
        rows.append({
            "check": f"COT {etf} ({H2_MARKETS[etf]})",
            "value": f"{len(r)} reports {r['as_of'].iloc[0].date()} to {r['as_of'].iloc[-1].date()}; "
            f"156th usable from {eligible.date() if eligible is not None else 'never'}; "
            f"{int(r['as_of'].isin(late).sum())} published late",
        })  # fmt: skip
    s3a_s0 = pd.read_csv(S3A_TRIALS).set_index("book").loc["S0", "sharpe_excess"]
    here = s1t.measure(s0_2008, panel)["sharpe_excess"]
    rows.append({"check": "S0 from 2008-01-02: excess Sharpe here, and in S3a's results",
                 "value": f"{here:.6f} here, {s3a_s0:.6f} in S3a's, {'equal' if abs(here - s3a_s0) < 1e-9 else 'DIFFERENT'}"})  # fmt: skip
    return pd.DataFrame(rows)


def h2_decision_signals(events: pd.DataFrame, panel: pd.DataFrame, reports) -> pd.DataFrame:
    """At each of H2's primary decisions: per asset, eligible or not, its latest usable
    hedging pressure, the 156-report median and the signal."""
    rows = []
    for decided in events["decided"]:
        history = panel.loc[:decided]
        eligible, usable = h2_eligible(history, reports)
        signal = h2_signals(H2_PRIMARY, {a: usable[a] for a in eligible}) if eligible else {}
        for a in H2_MARKETS:
            u = usable[a]
            rows.append({"decided": decided, "asset": a, "eligible": a in eligible,
                         "latest_report": u["as_of"].iloc[-1].date().isoformat() if len(u) else None,
                         "hp": float(u["hp"].iloc[-1]) if len(u) else math.nan,
                         "hp_median_156": float(u["hp"].iloc[-COT_HISTORY:].median()) if len(u) else math.nan,
                         "signal": signal.get(a, math.nan)})  # fmt: skip
    return pd.DataFrame(rows)


# ── stages ────────────────────────────────────────────────────────────


def _inputs():
    _cached()
    panel, volumes, _ = s1t.load_panel()
    return panel, volumes, term_structure(), s1t.s0_targets(), cot_reports(), cot_reports(late=False)


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, ts, targets, reports, reports_7day = _inputs()
    ratio = ts["ratio"]

    h1_results = h1_books(panel, volumes, ratio, targets, holdout=s1t.HOLDOUT_ROWS)
    h2_results = h2_books(panel, volumes, targets, reports, reports_7day, holdout=s1t.HOLDOUT_ROWS)
    h1_daily, h2_daily = on_common_days(h1_results), on_common_days(h2_results)
    h2_daily[H2_MIX] = 0.5 * h2_daily[S0_NAME] + 0.5 * h2_daily[H2_NAME]

    trial_table = trials(h1_daily, h2_daily, panel)
    trial_table.to_csv(RESULTS / "trials.csv", index=False)
    trial_sharpes = trial_table["sharpe_excess"].tolist()
    variance = float(np.var(trial_sharpes, ddof=1))
    pd.DataFrame([{"n_trials": N_TRIALS, "sharpe_variance_annualized": variance,
                   "sharpe_variance_per_day": variance / TRADING_DAYS}]).to_csv(
        RESULTS / "deflated_sharpe_inputs.csv", index=False)  # fmt: skip
    data_checks(panel, ts, reports, h2_daily[S0_NAME]).to_csv(RESULTS / "data_checks.csv", index=False)

    h1_table = book_rows(h1_results, h1_daily, panel, trial_sharpes,
                         {"spy_held": SPY_HELD, "s0": S0_NAME, "s1_blend_long_flat": S1_NAME})  # fmt: skip
    h1_table.to_csv(RESULTS / "walkforward_h1_books.csv", index=False)
    h2_table = book_rows(h2_results, h2_daily, panel, trial_sharpes, {"s0": S0_NAME, "s1_blend_long_flat": S1_NAME})
    mix = mix_record(h2_results[S0_NAME].combined, h2_results[H2_NAME].combined)
    for k, v in mix.items():
        h2_table.loc[h2_table["book"] == H2_MIX, k] = v
    h2_table.to_csv(RESULTS / "walkforward_h2_books.csv", index=False)

    pd.DataFrame({**h1_daily, "BIL": panel[CASH].pct_change(fill_method=None).reindex(h1_daily[S0_NAME].index)}).to_csv(
        RESULTS / "walkforward_daily_returns_h1.csv")  # fmt: skip
    pd.DataFrame({**h2_daily, "BIL": panel[CASH].pct_change(fill_method=None).reindex(h2_daily[S0_NAME].index)}).to_csv(
        RESULTS / "walkforward_daily_returns_h2.csv")  # fmt: skip
    h1_results[H1_NAME].events.to_csv(RESULTS / "walkforward_h1_primary_decisions.csv", index=False)
    h1_results[H1B_NAME].events.to_csv(RESULTS / "walkforward_h1b_decisions.csv", index=False)
    h2_results[H2_NAME].events.to_csv(RESULTS / "walkforward_h2_primary_decisions.csv", index=False)
    h2_decision_signals(h2_results[H2_NAME].events, panel, reports).to_csv(
        RESULTS / "walkforward_h2_primary_signals.csv", index=False)  # fmt: skip

    stress = pd.DataFrame([
        {"window": label, **{name: s1t.period_return(r, a, b) for name, r in h1_daily.items()}}
        for label, (a, b) in STRESS.items()
    ])  # fmt: skip
    stress.to_csv(RESULTS / "walkforward_h1_stress.csv", index=False)
    regimes = pd.DataFrame([
        {"period": label, **{name: s1t.period_return(r, a, b) for name, r in h2_daily.items()}}
        for label, (a, b) in s1t.REGIMES.items()
    ])  # fmt: skip
    regimes.to_csv(RESULTS / "walkforward_h2_regimes.csv", index=False)

    boot_h1 = paired_bootstrap(h1_daily[H1_NAME], h1_daily[SPY_HELD], panel, slack=0.0)
    boot_h1b = paired_bootstrap(h1_daily[H1B_NAME], h1_daily[S0_NAME], panel, slack=SHARPE_SLACK)
    boot_h2 = s1t.block_bootstrap(s1t.excess_returns(h2_daily[H2_NAME], panel))
    pd.DataFrame([{"book": H1_NAME, "against": SPY_HELD, **boot_h1},
                  {"book": H1B_NAME, "against": S0_NAME, **boot_h1b}]).to_csv(
        RESULTS / "walkforward_paired_bootstrap.csv", index=False)  # fmt: skip
    pd.DataFrame([{"book": H2_NAME, "percentile": p, "sharpe_excess": float(np.percentile(boot_h2, p))}
                  for p in (5, 25, 50, 75, 95)]).to_csv(RESULTS / "walkforward_h2_bootstrap.csv", index=False)  # fmt: skip

    criteria = []
    b1, spy = h1_table.set_index("book").loc[H1_NAME], h1_table.set_index("book").loc[SPY_HELD]
    s = stress.set_index("window")
    h1_margin = (s[H1_NAME] - s[SPY_HELD]).min()
    variants_vs_spy = min(h1_table.set_index("book").loc[H1_NAMES[v], "sharpe_excess"] for v in H1_VARIANTS) - spy["sharpe_excess"]
    criteria += [("H1", H1_NAME, c, float(v), bool(p)) for c, v, p in [
        ("1. excess Sharpe >= 0.40", b1["sharpe_excess"], b1["sharpe_excess"] >= 0.40),
        ("2. deflated Sharpe >= 0.90 (n 23)", b1["deflated_sharpe"], b1["deflated_sharpe"] >= 0.90),
        ("3. bootstrap P(excess Sharpe >= SPY's) >= 80%", boot_h1["p_sharpe_at_least_benchmark_less_slack"],
         boot_h1["p_sharpe_at_least_benchmark_less_slack"] >= 0.80),
        ("4. max drawdown at most 60% of SPY's", b1["max_drawdown"] / spy["max_drawdown"],
         abs(b1["max_drawdown"]) <= 0.60 * abs(spy["max_drawdown"])),
        ("5. every variant's excess Sharpe >= SPY's (worst variant less SPY)", variants_vs_spy, variants_vs_spy >= 0),
        ("6. cost drag <= 1% a year", b1["cost_drag"], b1["cost_drag"] <= 0.01),
        ("7. loses less than SPY in each stress window (smallest margin)", h1_margin, bool((s[H1_NAME] > s[SPY_HELD]).all())),
        ("8. no day outside the mandate", b1["mandate_breach_days"], b1["mandate_breach_days"] == 0),
    ]]  # fmt: skip
    bb, s0 = h1_table.set_index("book").loc[H1B_NAME], h1_table.set_index("book").loc[S0_NAME]
    h1b_margin = (s[H1B_NAME] - s[S0_NAME]).min()
    criteria += [("H1b", H1B_NAME, c, float(v), bool(p)) for c, v, p in [
        ("1. max drawdown at most 60% of S0's", bb["max_drawdown"] / s0["max_drawdown"],
         abs(bb["max_drawdown"]) <= 0.60 * abs(s0["max_drawdown"])),
        ("2. excess Sharpe >= S0's - 0.05", bb["sharpe_excess"] - s0["sharpe_excess"],
         bb["sharpe_excess"] >= s0["sharpe_excess"] - SHARPE_SLACK),
        ("3. bootstrap P(drawdown shallower than S0's) >= 80%", boot_h1b["p_drawdown_shallower"],
         boot_h1b["p_drawdown_shallower"] >= 0.80),
        ("4. bootstrap P(excess Sharpe >= S0's - 0.05) >= 80%", boot_h1b["p_sharpe_at_least_benchmark_less_slack"],
         boot_h1b["p_sharpe_at_least_benchmark_less_slack"] >= 0.80),
        ("5. cost drag <= 0.5% a year", bb["cost_drag"], bb["cost_drag"] <= 0.005),
        ("6. loses less than S0 in each of H1's stress windows (smallest margin)", h1b_margin,
         bool((s[H1B_NAME] > s[S0_NAME]).all())),
        ("7. no day outside the mandate", bb["mandate_breach_days"], bb["mandate_breach_days"] == 0),
    ]]  # fmt: skip
    t2 = h2_table.set_index("book")
    b2, s0_2, mix_row = t2.loc[H2_NAME], t2.loc[S0_NAME], t2.loc[H2_MIX]
    g = regimes.set_index("period")
    worst_variant = min(t2.loc[H2_NAMES[v], "sharpe_excess"] for v in H2_VARIANTS)
    p5 = float(np.percentile(boot_h2, 5))
    criteria += [("H2", H2_NAME, c, float(v), bool(p)) for c, v, p in [
        ("1. excess Sharpe >= 0.40", b2["sharpe_excess"], b2["sharpe_excess"] >= 0.40),
        ("2. deflated Sharpe >= 0.90 (n 23)", b2["deflated_sharpe"], b2["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile of the excess Sharpe > 0", p5, p5 > 0),
        ("4. every variant's excess Sharpe > 0 (worst variant)", worst_variant, worst_variant > 0),
        ("5. max drawdown no worse than -20%", b2["max_drawdown"], b2["max_drawdown"] >= -0.20),
        ("6a. positive in 2008", g.loc["2008", H2_NAME], g.loc["2008", H2_NAME] > 0),
        ("6b. positive in 2022", g.loc["2022", H2_NAME], g.loc["2022", H2_NAME] > 0),
        ("7a. correlation with S0 <= 0.50", b2["correlation_with_s0"], b2["correlation_with_s0"] <= 0.50),
        ("7b. 50/50 S0 + H2's excess Sharpe > S0's (difference)", mix_row["sharpe_excess"] - s0_2["sharpe_excess"],
         mix_row["sharpe_excess"] > s0_2["sharpe_excess"]),
        ("8. cost drag <= 1% a year", b2["cost_drag"], b2["cost_drag"] <= 0.01),
    ]]  # fmt: skip
    pd.DataFrame(criteria, columns=["hypothesis", "book", "criterion", "value", "pass"]).to_csv(
        RESULTS / "walkforward_criteria.csv", index=False)  # fmt: skip
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, ts, targets, reports, reports_7day = _inputs()
    start = panel.index[-s1t.HOLDOUT_ROWS]
    h1_results = h1_books(panel, volumes, ts["ratio"], targets, holdout=0, variants=False)
    h2_results = h2_books(panel, volumes, targets, reports, reports_7day, holdout=0, variants=False)
    h1_daily = {k: v.loc[start:] for k, v in on_common_days(h1_results).items()}
    h2_daily = {k: v.loc[start:] for k, v in on_common_days(h2_results).items()}
    h2_daily[H2_MIX] = 0.5 * h2_daily[S0_NAME] + 0.5 * h2_daily[H2_NAME]

    tables = []
    for window, daily, refs in (("H1", h1_daily, {"spy_held": SPY_HELD, "s0": S0_NAME, "s1_blend_long_flat": S1_NAME}),
                                ("H2", h2_daily, {"s0": S0_NAME, "s1_blend_long_flat": S1_NAME})):  # fmt: skip
        t = pd.DataFrame([{"window": window, "book": k, **s1t.measure(v, panel),
                           **{f"correlation_with_{label}": float(v.corr(daily[ref])) for label, ref in refs.items()}}
                          for k, v in daily.items()])  # fmt: skip
        tables.append(t)
    books = pd.concat(tables, ignore_index=True)
    books.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame(h1_daily).to_csv(RESULTS / "holdout_daily_returns_h1.csv")
    pd.DataFrame(h2_daily).to_csv(RESULTS / "holdout_daily_returns_h2.csv")

    h1t = books[books["window"] == "H1"].set_index("book")
    h2t = books[books["window"] == "H2"].set_index("book")
    b1, spy, bb, s0, b2 = h1t.loc[H1_NAME], h1t.loc[SPY_HELD], h1t.loc[H1B_NAME], h1t.loc[S0_NAME], h2t.loc[H2_NAME]
    rows = [
        {"hypothesis": "H1", "book": H1_NAME, "rule": "killed if its excess Sharpe is more than 0.5 below SPY's "
         "or its drawdown deeper than SPY's", "sharpe_vs_benchmark": b1["sharpe_excess"] - spy["sharpe_excess"],
         "drawdown_vs_benchmark": b1["max_drawdown"] - spy["max_drawdown"],
         "killed": bool(b1["sharpe_excess"] < spy["sharpe_excess"] - 0.5 or b1["max_drawdown"] < spy["max_drawdown"])},
        {"hypothesis": "H1b", "book": H1B_NAME, "rule": "killed if its drawdown is deeper than S0's or its excess "
         "Sharpe more than 0.5 below S0's", "sharpe_vs_benchmark": bb["sharpe_excess"] - s0["sharpe_excess"],
         "drawdown_vs_benchmark": bb["max_drawdown"] - s0["max_drawdown"],
         "killed": bool(bb["max_drawdown"] < s0["max_drawdown"] or bb["sharpe_excess"] < s0["sharpe_excess"] - 0.5)},
        {"hypothesis": "H2", "book": H2_NAME, "rule": "killed if its excess Sharpe is below -0.5 or its drawdown "
         "worse than -15%", "sharpe_vs_benchmark": b2["sharpe_excess"], "drawdown_vs_benchmark": b2["max_drawdown"],
         "killed": bool(b2["sharpe_excess"] < -0.5 or b2["max_drawdown"] < -0.15)},
    ]  # fmt: skip
    holdout = pd.DataFrame(rows)
    holdout.to_csv(RESULTS / "holdout_criteria.csv", index=False)
    pd.DataFrame(cboe_coverage(ts, panel.index[-s1t.HOLDOUT_ROWS :])).to_csv(
        RESULTS / "holdout_data_checks.csv", index=False)  # fmt: skip

    # the verdict, by HYPOTHESIS.md's rule: every criterion of the primary, and no holdout kill
    wf = pd.read_csv(RESULTS / "walkforward_criteria.csv")
    verdicts = []
    for hypothesis in ("H1", "H1b", "H2"):
        c = wf[wf["hypothesis"] == hypothesis]
        killed = bool(holdout.set_index("hypothesis").loc[hypothesis, "killed"])
        failed = c.loc[~c["pass"].astype(bool), "criterion"].tolist()
        verdicts.append({"hypothesis": hypothesis, "criteria_passed": int(c["pass"].astype(bool).sum()),
                         "criteria": len(c), "failed": "; ".join(failed), "holdout_killed": killed,
                         "verdict": "pass" if not failed and not killed else "fail"})  # fmt: skip
    pd.DataFrame(verdicts).to_csv(RESULTS / "verdict.csv", index=False)
    print(f"holdout from {start.date()} written to {RESULTS}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", choices=("all", "walkforward", "holdout"), default="all")
    stage = parser.parse_args().stage
    if stage in ("all", "walkforward"):
        walkforward_stage()
    if stage in ("all", "holdout"):
        holdout_stage()


if __name__ == "__main__":
    main()
