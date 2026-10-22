"""
r1_calendar_macro.py — R1 through the research gate: SPY on the turn of the month (the
primary), with FOMC days (V1) and with FOMC, jobs and CPI days (V2), else BIL, and the
reference books. HYPOTHESIS.md has the rules, costs, split and criteria; REPORT.md the
verdict.

    python r1_calendar_macro.py                      # the walk-forward, then the holdout: every number
    python r1_calendar_macro.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python r1_calendar_macro.py --stage holdout      # the holdout

Use the repo's .venv Python. Reuses research/s1_trend's panel, harness and measures, so the
numbers compare with S1's, S3a's, R2's and R3's. Reads the dates fetch.py cached in
research/_data/ (it downloads them only if they're missing) and writes results/.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s1_trend"))
import fetch  # noqa: E402
import s1_trend as s1t  # noqa: E402
from dv02_engine.costs import CostModel  # noqa: E402
from dv02_engine.walkforward import walk_forward  # noqa: E402

RESULTS = HERE / "results"
R3_TRIALS = HERE.parent / "r3_vix_futures" / "results" / "trials.csv"
R2_BOOKS = HERE.parent / "r2_vol_positioning" / "results" / "walkforward_h2_books.csv"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
PANEL_END = "2026-10-16"  # R2's and R3's panel: the holdout is its last 756 sessions, from 2023-10-12
FIRST_DECISION = s1t.FIRST_DECISION  # 2008-01-02
N_TRIALS = 30  # S1's 8 + S3a's 6 + R2's 9 + R3's 4 + these 3

# HYPOTHESIS.md's costs (amended at review, before any run): S1's CostModel with SPY's and
# BIL's half-spreads at 1.0 bps
COSTS = CostModel(
    half_spread_bps=2.0,
    half_spread_bps_by_symbol={"SPY": 1.0, "BIL": 1.0},
    impact_k=0.5,
    borrow_bps=25.0,
    borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
    cash_interest_bps=0.0,  # as S1's: idle money sits in BIL
)

STRESS = {
    "2008 crisis (Sep 15 to Nov 20)": ("2008-09-15", "2008-11-20"),
    "2010 flash crash (Apr 23 to Jul 2)": ("2010-04-23", "2010-07-02"),
    "2011 (Jul 22 to Oct 3)": ("2011-07-22", "2011-10-03"),
    "2015-16 (Aug 17 to Feb 11)": ("2015-08-17", "2016-02-11"),
    "February 2018 (Jan 26 to Feb 9)": ("2018-01-26", "2018-02-09"),
    "late 2018 (Sep 20 to Dec 24)": ("2018-09-20", "2018-12-24"),
    "2020 crash (Feb 19 to Mar 23)": ("2020-02-19", "2020-03-23"),
    "2022": ("2022-01-01", "2022-12-31"),
}
PRIMARY = "R1 primary: SPY on the turn of the month"
V1 = "R1 V1: turn of the month + FOMC days"
V2 = "R1 V2: turn of the month + FOMC, jobs and CPI days"
VARIANTS = (V1, V2)
COMPLEMENT = "the complement: SPY on every other session (reference)"
SPY_HELD, S0_NAME, S1_NAME = "SPY held", "S0", "S1 blend long/flat"
MIX = "50/50 S0 + R1 primary"


# ── the calendar (fetch.py) ───────────────────────────────────────────

# clock.py's sessions, well past the panel's end so "two sessions ahead" is defined there too
DAYS = fetch.sessions("2004-12-01", "2027-01-31")
TOM = fetch.turn_of_month(DAYS)
ANNOUNCEMENTS = fetch.announcement_days(DAYS)
CHOSEN = {
    PRIMARY: TOM,
    V1: TOM | ANNOUNCEMENTS["fomc"],
    V2: TOM | ANNOUNCEMENTS["fomc"] | ANNOUNCEMENTS["employment"] | ANNOUNCEMENTS["cpi"],
}


def two_ahead(t: pd.Timestamp) -> date:
    """The second session after t on clock.py's calendar: the one whose close-to-close
    return a decision at t's close earns (lag 1: traded at the next close, held one more)."""
    first = fetch.next_session(t.date() + timedelta(days=1))
    return fetch.next_session(first + timedelta(days=1))


def calendar(chosen: set[date], *, other_days: bool = False):
    """SPY at 99.5% when the session two ahead is chosen (or, other_days, when it isn't),
    else BIL at 99.5%; decided every session."""

    def strategy(history: pd.DataFrame) -> dict[str, float]:
        hit = (two_ahead(history.index[-1]) in chosen) != other_days
        return {"SPY": BUDGET} if hit else {CASH: BUDGET}

    return strategy


def spy_held():
    """SPY bought at 99.5% at the first trade and never rebalanced (R2's benchmark)."""
    state = {"bought": False}

    def strategy(history: pd.DataFrame):
        if state["bought"]:
            return None
        state["bought"] = True
        return {"SPY": BUDGET}

    return strategy


# ── running and measuring ─────────────────────────────────────────────


def inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    panel, volumes, _ = s1t.load_panel()
    return panel.loc[:PANEL_END], volumes.loc[:PANEL_END]


def run(panel, volumes, strategy, *, rebalance_every: int, holdout: int, costs: CostModel = COSTS):
    """S1's harness (lag 1, the mandate check, yearly test blocks) with this study's costs."""
    first = int(panel.index.searchsorted(pd.Timestamp(FIRST_DECISION)))
    return walk_forward(panel, strategy, train=first, test=s1t.TEST_ROWS, costs=costs, n_trials=N_TRIALS,
                        holdout=holdout, rebalance_every=rebalance_every, lag=1, volumes=volumes,
                        mandate=True)  # fmt: skip


def books(panel, volumes, *, holdout: int, variants: bool = True) -> dict[str, object]:
    targets = s1t.s0_targets()
    plan = {PRIMARY: (calendar(CHOSEN[PRIMARY]), 1), COMPLEMENT: (calendar(CHOSEN[PRIMARY], other_days=True), 1),
            SPY_HELD: (spy_held(), 1), S0_NAME: (s1t.s0(targets), 1),
            S1_NAME: (s1t.s1("blend", "long_flat"), s1t.REBALANCE_EVERY)}  # fmt: skip
    if variants:
        plan.update({v: (calendar(CHOSEN[v]), 1) for v in VARIANTS})
    return {name: run(panel, volumes, strategy, rebalance_every=every, holdout=holdout)
            for name, (strategy, every) in plan.items()}  # fmt: skip


def on_common_days(results: dict[str, object]) -> dict[str, pd.Series]:
    daily = {name: wf.equity["daily_return"] for name, wf in results.items()}
    common = None
    for r in daily.values():
        common = r.index if common is None else common.intersection(r.index)
    return {name: r.loc[common] for name, r in daily.items()}


def worst_month(daily: pd.Series) -> tuple[float, str]:
    months = (1 + daily).groupby(daily.index.to_period("M")).prod() - 1
    return float(months.min()), str(months.idxmin())


def in_spy(events: pd.DataFrame) -> pd.Series:
    """Decisions holding SPY, by the year of the session they earn."""
    spy = events["SPY"].fillna(0.0) if "SPY" in events.columns else pd.Series(0.0, index=events.index)
    years = [two_ahead(pd.Timestamp(t)).year for t in events["decided"]]
    return (spy > 0).groupby(years).sum()


def book_rows(results, daily, panel, trial_sharpes) -> pd.DataFrame:
    rows = []
    for name, r in daily.items():
        row = {"book": name, **s1t.measure(r, panel)}
        wm, wm_at = worst_month(r)
        row.update(worst_day=float(r.min()), worst_day_date=r.idxmin().date().isoformat(), worst_month=wm,
                   worst_month_at=wm_at)  # fmt: skip
        dsr, threshold = s1t.deflated(s1t.excess_returns(r, panel), N_TRIALS, trial_sharpes)
        if name in results:
            c, events = results[name].combined, results[name].events
            metrics = results[name].backtest["backtest_metrics"].set_index("metric")["value"]
            row.update(turnover=c["turnover"], cost_drag=c["cost_drag"], total_cost=c["total_cost"],
                       spread_cost=c["spread_cost"], commission_cost=c["commission_cost"],
                       impact_cost=c["impact_cost"], borrow_cost=c["borrow_cost"],
                       unmeasured_impact_trades=float(metrics.get("unmeasured_impact_trades", math.nan)),
                       mandate_breach_days=c["mandate_breach_days"], max_gross_exposure=c["max_gross_exposure"],
                       decisions=len(events))  # fmt: skip
            if name in CHOSEN or name == COMPLEMENT:
                held = events["SPY"].fillna(0.0) > 0 if "SPY" in events.columns else pd.Series(False, index=events.index)
                row.update(share_of_decisions_in_spy=float(held.mean()),
                           entries_into_spy=int((held & ~held.shift(1, fill_value=False)).sum()))  # fmt: skip
        row.update(deflated_sharpe=dsr, deflated_sharpe_threshold=threshold)
        row["correlation_with_s0"] = float(r.corr(daily[S0_NAME]))
        row["correlation_with_s1_blend_long_flat"] = float(r.corr(daily[S1_NAME]))
        rows.append(row)
    return pd.DataFrame(rows)


def mix_record(c0: dict, c1: dict) -> dict[str, float]:
    """A 50/50 book's turnover, cost drag and mandate record, as S3a's, R2's and R3's: half of each."""
    return {
        "turnover": 0.5 * c0["turnover"] + 0.5 * c1["turnover"],
        "cost_drag": 0.5 * c0["cost_drag"] + 0.5 * c1["cost_drag"],
        "mandate_breach_days": c0["mandate_breach_days"] + c1["mandate_breach_days"],
    }


def trials(daily, panel) -> pd.DataFrame:
    """The 30 trials: R3's file lists the first 27; these 3 from this run."""
    earlier = pd.read_csv(R3_TRIALS)
    ours = pd.DataFrame([{"study": "R1", "trial": n, "sharpe_excess": s1t.measure(daily[n], panel)["sharpe_excess"]}
                         for n in (PRIMARY, *VARIANTS)])  # fmt: skip
    out = pd.concat([earlier, ours], ignore_index=True)
    assert len(out) == N_TRIALS, len(out)
    return out


def decisions(events: pd.DataFrame) -> pd.DataFrame:
    """The primary's decisions, with the session each earns and which turn-of-month day it is."""
    rows = []
    for t in pd.to_datetime(events["decided"]):
        target = two_ahead(t)
        month = [d for d in DAYS if (d.year, d.month) == (target.year, target.month)]
        if target == month[-1]:
            label = "-1"
        elif target in month[:3]:
            label = f"+{month.index(target) + 1}"
        else:
            label = ""
        rows.append({"earns_session": target.isoformat(), "turn_of_month_day": label})
    return pd.concat([events.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def calendar_checks(panel: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, str]]:
    """The panel's sessions against clock.py's, and the chosen days each rule has in the window."""
    have = {d.date() for d in panel.index}
    rule = {d for d in DAYS if panel.index[0].date() <= d <= panel.index[-1].date()}
    window = [d for d in DAYS if start.date() <= d <= end.date()]
    out = [{"check": "sessions by clock.py's rules the panel lacks (unscheduled closures)",
            "value": "; ".join(str(d) for d in sorted(rule - have))},
           {"check": "panel days clock.py's rules don't call sessions",
            "value": "; ".join(str(d) for d in sorted(have - rule)) or "none"}]  # fmt: skip
    for name, chosen in CHOSEN.items():
        n = sum(d in chosen for d in window)
        out.append({"check": f"{name}: chosen sessions {window[0]} to {window[-1]}",
                    "value": f"{n} of {len(window)} ({n / len(window):.1%})"})  # fmt: skip
    return out


# ── stages ────────────────────────────────────────────────────────────


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes = inputs()
    results = books(panel, volumes, holdout=s1t.HOLDOUT_ROWS)
    daily = on_common_days(results)
    daily[MIX] = 0.5 * daily[S0_NAME] + 0.5 * daily[PRIMARY]

    trial_table = trials(daily, panel)
    trial_table.to_csv(RESULTS / "trials.csv", index=False)
    trial_sharpes = trial_table["sharpe_excess"].tolist()
    variance = float(np.var(trial_sharpes, ddof=1))
    pd.DataFrame([{"n_trials": N_TRIALS, "sharpe_variance_annualized": variance,
                   "sharpe_variance_per_day": variance / TRADING_DAYS}]).to_csv(
        RESULTS / "deflated_sharpe_inputs.csv", index=False)  # fmt: skip

    table = book_rows(results, daily, panel, trial_sharpes)
    for k, v in mix_record(results[S0_NAME].combined, results[PRIMARY].combined).items():
        table.loc[table["book"] == MIX, k] = v
    table.to_csv(RESULTS / "walkforward_books.csv", index=False)
    pd.DataFrame({**daily, "BIL": panel[CASH].pct_change(fill_method=None).reindex(daily[S0_NAME].index),
                  "SPY": panel["SPY"].pct_change(fill_method=None).reindex(daily[S0_NAME].index)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip
    decisions(results[PRIMARY].events).to_csv(RESULTS / "walkforward_primary_decisions.csv", index=False)
    pd.DataFrame({name: in_spy(results[name].events) for name in (*CHOSEN, COMPLEMENT)}).rename_axis(
        "year").to_csv(RESULTS / "walkforward_days_in_spy.csv")  # fmt: skip

    stress = pd.DataFrame([{"window": label, **{name: s1t.period_return(r, a, b) for name, r in daily.items()}}
                           for label, (a, b) in STRESS.items()])  # fmt: skip
    stress.to_csv(RESULTS / "walkforward_stress.csv", index=False)
    boot = s1t.block_bootstrap(s1t.excess_returns(daily[PRIMARY], panel))
    pd.DataFrame([{"book": PRIMARY, "percentile": p, "sharpe_excess": float(np.percentile(boot, p))}
                  for p in (5, 25, 50, 75, 95)]).to_csv(RESULTS / "walkforward_bootstrap.csv", index=False)  # fmt: skip

    # the data: S0 with S1's own costs must score R2's 0.268481; this study's costs move it a little
    s0_s1_costs = run(panel, volumes, s1t.s0(s1t.s0_targets()), rebalance_every=1, holdout=s1t.HOLDOUT_ROWS,
                      costs=s1t.COSTS).equity["daily_return"].loc[daily[S0_NAME].index]  # fmt: skip
    s0_check = s1t.measure(s0_s1_costs, panel)["sharpe_excess"]
    r2_s0 = pd.read_csv(R2_BOOKS).set_index("book").loc["S0", "sharpe_excess"]
    s0_here = s1t.measure(daily[S0_NAME], panel)["sharpe_excess"]
    test_days = daily[S0_NAME].index
    checks = [{"check": "panel", "value": f"{panel.index[0].date()} to {panel.index[-1].date()}, {len(panel)} sessions; "
               f"holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()}"},
              *calendar_checks(panel, test_days[0], test_days[-1]),
              {"check": "S0 from 2008-01-02 with S1's costs: excess Sharpe here, and in R2's results",
               "value": f"{s0_check:.6f} here, {r2_s0:.6f} in R2's, {'equal' if abs(s0_check - r2_s0) < 1e-9 else 'DIFFERENT'}"},
              {"check": "S0 from 2008-01-02 with this study's costs (SPY and BIL at 1.0 bps)",
               "value": f"{s0_here:.6f}"}]  # fmt: skip
    pd.DataFrame(checks).to_csv(RESULTS / "data_checks.csv", index=False)

    t = table.set_index("book")
    b, s0, mix = t.loc[PRIMARY], t.loc[S0_NAME], t.loc[MIX]
    s = stress.set_index("window")
    worst_variant = min(t.loc[v, "sharpe_excess"] for v in VARIANTS)
    p5 = float(np.percentile(boot, 5))
    margin = float((s[PRIMARY] - s[SPY_HELD]).min())
    criteria = [("R1", PRIMARY, c, float(v), bool(p)) for c, v, p in [
        ("1. excess Sharpe >= 0.40", b["sharpe_excess"], b["sharpe_excess"] >= 0.40),
        ("2. deflated Sharpe >= 0.90 (n 30)", b["deflated_sharpe"], b["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile of the excess Sharpe > 0", p5, p5 > 0),
        ("4. every variant's excess Sharpe > 0 (worst variant)", worst_variant, worst_variant > 0),
        ("5. max drawdown no worse than -20%", b["max_drawdown"], b["max_drawdown"] >= -0.20),
        ("6. cost drag <= 1% a year", b["cost_drag"], b["cost_drag"] <= 0.01),
        ("7. no day outside the mandate", b["mandate_breach_days"], b["mandate_breach_days"] == 0),
        ("8. worst calendar month no worse than -10%", b["worst_month"], b["worst_month"] >= -0.10),
        ("9. a return above SPY held's in each stress window (smallest margin)", margin,
         bool((s[PRIMARY] > s[SPY_HELD]).all())),
        ("10. 50/50 S0 + R1's excess Sharpe > S0's (difference)", mix["sharpe_excess"] - s0["sharpe_excess"],
         mix["sharpe_excess"] > s0["sharpe_excess"]),
    ]]  # fmt: skip
    pd.DataFrame(criteria, columns=["hypothesis", "book", "criterion", "value", "pass"]).to_csv(
        RESULTS / "walkforward_criteria.csv", index=False)  # fmt: skip
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes = inputs()
    start = panel.index[-s1t.HOLDOUT_ROWS]
    results = books(panel, volumes, holdout=0, variants=False)
    daily = {k: v.loc[start:] for k, v in on_common_days(results).items()}
    daily[MIX] = 0.5 * daily[S0_NAME] + 0.5 * daily[PRIMARY]

    rows = []
    for name, r in daily.items():
        wm, wm_at = worst_month(r)
        rows.append({"book": name, **s1t.measure(r, panel), "worst_day": float(r.min()),
                     "worst_day_date": r.idxmin().date().isoformat(), "worst_month": wm, "worst_month_at": wm_at,
                     "correlation_with_s0": float(r.corr(daily[S0_NAME])),
                     "correlation_with_s1_blend_long_flat": float(r.corr(daily[S1_NAME]))})  # fmt: skip
    table = pd.DataFrame(rows)
    table.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame({**daily, "SPY": panel["SPY"].pct_change(fill_method=None).reindex(daily[S0_NAME].index)}).to_csv(
        RESULTS / "holdout_daily_returns.csv")  # fmt: skip
    pd.DataFrame(calendar_checks(panel, daily[S0_NAME].index[0], daily[S0_NAME].index[-1])).to_csv(
        RESULTS / "holdout_data_checks.csv", index=False)  # fmt: skip

    b = table.set_index("book").loc[PRIMARY]
    killed = bool(b["sharpe_excess"] < -0.5 or b["max_drawdown"] < -0.15)
    pd.DataFrame([{"hypothesis": "R1", "book": PRIMARY,
                   "rule": "killed if its excess Sharpe is below -0.5 or its drawdown worse than -15%",
                   "sharpe_excess": b["sharpe_excess"], "max_drawdown": b["max_drawdown"],
                   "killed": killed}]).to_csv(RESULTS / "holdout_criteria.csv", index=False)  # fmt: skip

    # the verdict, by HYPOTHESIS.md's rule: every criterion of the primary, and no holdout kill
    wf = pd.read_csv(RESULTS / "walkforward_criteria.csv")
    failed = wf.loc[~wf["pass"].astype(bool), "criterion"].tolist()
    pd.DataFrame([{"hypothesis": "R1", "criteria_passed": int(wf["pass"].astype(bool).sum()), "criteria": len(wf),
                   "failed": "; ".join(failed), "holdout_killed": killed,
                   "verdict": "pass" if not failed and not killed else "fail"}]).to_csv(
        RESULTS / "verdict.csv", index=False)  # fmt: skip
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
