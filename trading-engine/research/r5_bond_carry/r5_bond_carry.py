"""
r5_bond_carry.py — R5 through the research gate: TLT while its carry over the 3-month bill is
positive, else BIL (the primary); IEI (V1) and SHY (V2) the same on their own carry; and the
reference books. HYPOTHESIS.md has the rules, costs, split and criteria, amended at review
before any run.

    python r5_bond_carry.py                      # the walk-forward, then the holdout: every number
    python r5_bond_carry.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python r5_bond_carry.py --stage holdout      # the holdout

Use the repo's .venv Python. Every book is on total returns (HYPOTHESIS.md, "The instrument"):
the bond ETFs and S0's other distribution payers enter the C++ walk-forward under their own
symbols as total-return closes (data.tr_closes), with their volumes rescaled so the impact sees
the true dollar volume. S0 on the studies' usual basis runs too, as the harness check. Reuses
research/s1_trend's panel, harness and measures, so the numbers compare with the earlier
studies'. Reads the yields and prices through data.py (the yields are cached in
research/_data/; nothing is downloaded once they are) and writes results/.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s1_trend"))
sys.path.insert(0, str(HERE.parents[1]))  # trading-engine, for the NYSE calendar
import data  # noqa: E402
import s1_trend as s1t  # noqa: E402
from dv02_engine.costs import CostModel  # noqa: E402
from dv02_engine.walkforward import walk_forward  # noqa: E402
from src.common import clock  # noqa: E402

RESULTS = HERE / "results"
R4_TRIALS = HERE.parent / "r4_overnight" / "results" / "trials.csv"
R4_BOOKS = HERE.parent / "r4_overnight" / "results" / "walkforward_books.csv"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
PANEL_END = "2026-10-16"  # the earlier studies' panel: the holdout is its last 756 sessions, from 2023-10-12
FIRST_DECISION = pd.Timestamp(s1t.FIRST_DECISION)  # 2008-01-02
N_TRIALS = 36  # S1's 8 + S3a's 6 + R2's 9 + R3's 4 + R1's 3 + R4's 3 + these 3
# S0's ETFs that pay no distributions: their price is their total return, and they keep S1's
# panel's closes (HYPOTHESIS.md, "S0's ETFs"). Every other ETF S0 holds is on data.tr_closes.
NO_DISTRIBUTIONS = ("ETHA", "GLD", "IBIT", "SLV", "UNG", "USO", "VIXY")
EPISODE_TOLERANCE = 0.0025  # criterion 10: a window's return no more than 0.25 points below TLT held's


def cost_model(k: float, by_symbol: dict[str, float]) -> CostModel:
    """S1's CostModel with the half-spreads and the impact at k times; the commission stays on
    (the CostModel can't scale it)."""
    return CostModel(
        half_spread_bps=2.0 * k,
        half_spread_bps_by_symbol={s: v * k for s, v in by_symbol.items()},
        impact_k=0.5 * k,
        borrow_bps=25.0,
        borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
        cash_interest_bps=0.0,  # as S1's: idle money earns nothing
    )


R5_SPREADS = {"TLT": 1.0, "IEI": 2.0, "SHY": 1.0, "BIL": 1.0}  # HYPOTHESIS.md's half-spreads, bps
R1_SPREADS = {"SPY": 1.0, "BIL": 1.0}  # R1's, which R4's S0 used: the harness check's
COSTS = cost_model(1, R5_SPREADS)
R1_COSTS = cost_model(1, R1_SPREADS)
MULTIPLES = (0, 1, 3)

STRESS = {f"{name} ({a} to {b})": (a, b) for name, (a, b) in data.STRESS.items()}
# HYPOTHESIS.md's bond sell-offs, which data.sell_offs must find again
SELL_OFFS = [("2009-03-18", "2009-06-10"), ("2010-10-06", "2011-02-08"), ("2012-07-25", "2013-12-31"),
             ("2016-07-05", "2017-03-13"), ("2017-09-07", "2018-11-08"), ("2020-08-04", "2021-03-19"),
             ("2021-07-19", "2022-06-14"), ("2022-08-01", "2022-10-24"), ("2023-04-05", "2023-10-19")]  # fmt: skip
HOLDOUT_SELL_OFF = ("2024-09-16", "2025-01-13")

PRIMARY = "R5 primary: TLT while its carry is positive"
V1 = "R5 V1: IEI while its carry is positive"
V2 = "R5 V2: SHY while its carry is positive"
VARIANTS = (V1, V2)
TLT_HELD, IEI_HELD, SHY_HELD = "TLT held", "IEI held", "SHY held"
S0_TR = "S0_TR (S0 with its distributions)"
S0_USUAL = "S0 on the usual basis (the harness check)"
MIX = "50/50 S0_TR + R5 primary"
RULES = {PRIMARY: "TLT", V1: "IEI", V2: "SHY"}
HELD = {TLT_HELD: "TLT", IEI_HELD: "IEI", SHY_HELD: "SHY"}
OWN_HELD = {PRIMARY: TLT_HELD, V1: IEI_HELD, V2: SHY_HELD}


# ── the inputs ────────────────────────────────────────────────────────


def carry_table(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Each rule's carry, its segment's yield less the 3-month (points), on every session: the
    last yield carried over the bond market's holidays (data.segment_yields)."""
    seg = data.segment_yields(data.yields())
    seg = seg.reindex(seg.index.union(index)).ffill().reindex(index)
    out = pd.DataFrame({bond: seg[bond] - seg["bill"] for bond in RULES.values()})
    out[[f"{c}_yield" for c in seg.columns]] = seg.to_numpy()
    return out


def inputs():
    """S1's panel and volumes (the usual basis), the same with every distribution payer S0
    holds on its total-return closes and volumes rescaled to the true dollar volume (the
    total-return basis), the carry, and the payers."""
    panel, volumes, _ = s1t.load_panel()
    panel, volumes = panel.loc[:PANEL_END], volumes.loc[:PANEL_END]
    payers = [s for s in sorted(s1t.s0_targets()["symbol"].unique()) if s not in NO_DISTRIBUTIONS]
    tr_panel, tr_volumes = panel.copy(), volumes.copy()
    for s in payers:
        tr = data.tr_closes(s).loc[:PANEL_END]
        listed = panel.index[panel[s].notna()]
        if not tr.index.equals(listed):
            raise ValueError(f"{s}: the total-return closes' sessions aren't the panel's")
        close = data.master(s)["close"].reindex(tr.index)
        tr_panel[s] = tr.reindex(panel.index)
        tr_volumes[s] = (volumes[s].reindex(tr.index) * close / tr).reindex(panel.index)
    return panel, volumes, tr_panel, tr_volumes, carry_table(panel.index), payers


def decision_day(t: pd.Timestamp) -> bool:
    """The walk-forward's first decision day, or a month's last NYSE session by the exchange's
    calendar (known in advance)."""
    if t == FIRST_DECISION:
        return True
    d = t.date() + timedelta(days=1)
    while not clock.is_us_business_day(d):
        d += timedelta(days=1)
    return d.month != t.month


# ── the books ─────────────────────────────────────────────────────────


def carry_rule(bond: str, carry: pd.DataFrame, log: dict):
    """HYPOTHESIS.md's rule: on each decision day, the bond at 99.5% while its carry is above 0,
    else BIL at 99.5%. Reads the carry on the decision day and nothing after it."""
    signal = carry[bond]

    def strategy(history: pd.DataFrame):
        t = history.index[-1]
        if not decision_day(t):
            return None
        c = float(signal.loc[:t].iloc[-1])
        holds = bond if c > 0 else CASH
        log[t] = (c, holds)
        return {holds: BUDGET}

    return strategy


def held(bond: str):
    """The bond at 99.5%, re-sized on the same decision days."""

    def strategy(history: pd.DataFrame):
        return {bond: BUDGET} if decision_day(history.index[-1]) else None

    return strategy


def run(panel, volumes, strategy, *, holdout: int, costs: CostModel = COSTS):
    """S1's harness (lag 1, the mandate check, yearly test blocks); the strategies decide only
    on their decision days, so it is called on every row."""
    first = int(panel.index.searchsorted(FIRST_DECISION))
    return walk_forward(panel, strategy, train=first, test=s1t.TEST_ROWS, costs=costs, n_trials=N_TRIALS,
                        holdout=holdout, rebalance_every=1, lag=1, volumes=volumes, mandate=True)  # fmt: skip


def books(inp, *, holdout: int, names) -> tuple[dict[str, object], dict[str, dict]]:
    """Each named book's walk-forward, and the carry rules' decision logs."""
    panel, volumes, tr_panel, tr_volumes, carry, _ = inp
    logs: dict[str, dict] = {}
    out = {}
    for name in names:
        if name in RULES:
            logs[name] = {}
            out[name] = run(tr_panel, tr_volumes, carry_rule(RULES[name], carry, logs[name]), holdout=holdout)
        elif name in HELD:
            out[name] = run(tr_panel, tr_volumes, held(HELD[name]), holdout=holdout)
        elif name == S0_TR:
            out[name] = run(tr_panel, tr_volumes, s1t.s0(s1t.s0_targets()), holdout=holdout)
        elif name == S0_USUAL:
            out[name] = run(panel, volumes, s1t.s0(s1t.s0_targets()), holdout=holdout, costs=R1_COSTS)
    return out, logs


def on_common_days(results: dict[str, object]) -> dict[str, pd.Series]:
    daily = {name: wf.equity["daily_return"] for name, wf in results.items()}
    common = None
    for r in daily.values():
        common = r.index if common is None else common.intersection(r.index)
    return {name: r.loc[common] for name, r in daily.items()}


# ── measuring ─────────────────────────────────────────────────────────


def dated(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.DatetimeIndex(pd.to_datetime(out["date"]), name="date")
    return out.drop(columns="date")


def worst_month(daily: pd.Series) -> tuple[float, str]:
    months = (1 + daily).groupby(daily.index.to_period("M")).prod() - 1
    return float(months.min()), str(months.idxmin())


def holdings(wf, bond: str) -> pd.DataFrame:
    """Each decision's holding, from the harness's own events: the bond or BIL."""
    ev = wf.events.copy()
    cols = [c for c in (bond, CASH) if c in ev.columns]
    w = ev[cols].fillna(0.0)
    ev["holds"] = np.where(w.get(bond, 0.0) > 0, bond, CASH)
    return ev[["decided", "executed", "fold", "holds"]]


def in_bond(wf, bond: str, days: pd.DatetimeIndex) -> pd.Series:
    """Whether each session's return was earned in the bond: its position at the prior close."""
    pos = dated(wf.backtest["backtest_positions"])
    held_before = pos[bond].shift(1).fillna(0.0) if bond in pos.columns else pd.Series(0.0, index=pos.index)
    return (held_before > 0).reindex(days).fillna(False)


def book_rows(results, daily, bases: dict[str, pd.DataFrame], *, trial_sharpes=None) -> pd.DataFrame:
    """One row a book. bases: the total-return panel ("tr"), whose BIL_TR every R5 book's
    excess return is over, and S1's ("usual"), for S0 on the usual basis."""
    rows = []
    for name, r in daily.items():
        basis = bases["usual"] if name == S0_USUAL else bases["tr"]
        row = {"book": name, **s1t.measure(r, basis)}
        wm, wm_at = worst_month(r)
        row.update(worst_day=float(r.min()), worst_day_date=r.idxmin().date().isoformat(), worst_month=wm,
                   worst_month_at=wm_at)  # fmt: skip
        if trial_sharpes is not None and name != S0_USUAL:
            dsr, threshold = s1t.deflated(s1t.excess_returns(r, basis), N_TRIALS, trial_sharpes)
            row.update(deflated_sharpe=dsr, deflated_sharpe_threshold=threshold)
        if name in results:
            wf = results[name]
            res = dated(wf.backtest["backtest_results"]).loc[r.index]
            years = len(r) / TRADING_DAYS
            value = float(res["portfolio_value"].mean())
            costs = {k: float(res[f"daily_{k}_cost"].sum()) for k in ("commission", "spread", "impact")}
            total = float(res["daily_total_cost"].sum())
            row.update(cost_drag=total / years / value, total_cost=total, commission_cost=costs["commission"],
                       spread_cost=costs["spread"], impact_cost=costs["impact"],
                       mandate_breach_days=wf.combined["mandate_breach_days"],
                       max_gross_exposure=wf.combined["max_gross_exposure"], decisions=len(wf.events))  # fmt: skip
            bond = RULES.get(name) or HELD.get(name)
            if bond:
                h = holdings(wf, bond)
                decided = pd.to_datetime(h["decided"])
                inside = h[(decided >= r.index[0] - pd.Timedelta(days=7)) & (decided <= r.index[-1])]
                row.update(decisions_in_bond=int((inside["holds"] == bond).sum()),
                           decisions_in_bills=int((inside["holds"] == CASH).sum()),
                           sessions_in_bond=int(in_bond(wf, bond, r.index).sum()), sessions=len(r),
                           switches=int((inside["holds"] != inside["holds"].shift(1)).iloc[1:].sum()))  # fmt: skip
        row["correlation_with_s0_tr"] = float(r.corr(daily[S0_TR]))
        row["correlation_with_tlt_held"] = float(r.corr(daily[TLT_HELD]))
        rows.append(row)
    table = pd.DataFrame(rows)
    # the 50/50 book: half of each half's costs (as S3a's, R2's, R3's, R4's), both halves' mandate records
    t = table.set_index("book")
    if MIX in t.index:
        for k in ("cost_drag", "total_cost", "commission_cost", "spread_cost", "impact_cost"):
            table.loc[table["book"] == MIX, k] = 0.5 * t.loc[S0_TR, k] + 0.5 * t.loc[PRIMARY, k]
        table.loc[table["book"] == MIX, "mandate_breach_days"] = (
            t.loc[S0_TR, "mandate_breach_days"] + t.loc[PRIMARY, "mandate_breach_days"])  # fmt: skip
    return table


def trials(daily, tr_panel) -> pd.DataFrame:
    """The 36 trials: R4's file lists the first 33; these 3 from this run."""
    earlier = pd.read_csv(R4_TRIALS)
    ours = pd.DataFrame([{"study": "R5", "trial": n, "sharpe_excess": s1t.measure(daily[n], tr_panel)["sharpe_excess"]}
                         for n in (PRIMARY, *VARIANTS)])  # fmt: skip
    out = pd.concat([earlier, ours], ignore_index=True)
    assert len(out) == N_TRIALS, len(out)
    return out


def windows(last: pd.Timestamp, *, holdout: bool) -> list[dict]:
    """The stress windows: R3's eight and the bond sell-offs (data.sell_offs on FRED's 10-year,
    which must find HYPOTHESIS.md's list), each judged to ``last`` at the latest."""
    ten = data.yields()["DGS10"]
    found = [(a.date().isoformat(), b.date().isoformat()) for a, b in data.sell_offs(ten.loc["2007-06-01":PANEL_END])]
    expected = [*SELL_OFFS, HOLDOUT_SELL_OFF]
    if found != expected:
        raise ValueError(f"data.sell_offs found {found}, not HYPOTHESIS.md's {expected}")
    out = []
    if not holdout:
        for label, (a, b) in STRESS.items():
            out.append({"window": label, "kind": "R3's stress window", "start": a, "end": b, "judged_to": b})
        for a, b in SELL_OFFS:
            rise = (ten.loc[b] - ten.loc[a]) * 100
            out.append({"window": f"bond sell-off {a} to {b} (+{rise:.0f} bps)", "kind": "bond sell-off", "start": a,
                        "end": b, "judged_to": min(pd.Timestamp(b), last).date().isoformat()})  # fmt: skip
    else:
        a, b = HOLDOUT_SELL_OFF
        rise = (ten.loc[b] - ten.loc[a]) * 100
        out.append({"window": f"bond sell-off {a} to {b} (+{rise:.0f} bps)", "kind": "bond sell-off", "start": a,
                    "end": b, "judged_to": b})  # fmt: skip
    return out


def stress_table(daily: dict[str, pd.Series], wins: list[dict]) -> pd.DataFrame:
    rows = []
    for w in wins:
        rows.append({**w, **{name: s1t.period_return(r, w["start"], w["judged_to"]) for name, r in daily.items()}})
    return pd.DataFrame(rows)


def episodes(results, daily, rule: str) -> pd.DataFrame:
    """Each run of sessions whose return the rule earned in bills (BIL held at the prior close),
    against its own ETF held over the same sessions. A run's last session is the one its switch
    back filled, so that switch's cost is inside; the switch into bills filled the session
    before the run, and its cost isn't."""
    bond, own = RULES[rule], OWN_HELD[rule]
    days = daily[rule].index
    pos = dated(results[rule].backtest["backtest_positions"])
    if CASH not in pos.columns:
        return pd.DataFrame()
    in_bills = (pos[CASH].shift(1).fillna(0.0) > 0).reindex(days).fillna(False)
    run_id = (in_bills != in_bills.shift(1)).cumsum()[in_bills]
    h = holdings(results[rule], bond)
    h = h[h["holds"] != h["holds"].shift(1)]  # the first decision and every switch
    executed = pd.to_datetime(h["executed"])
    rows = []
    for _, span in run_id.groupby(run_id):
        span = span.index
        into = h[(executed < span[0]) & (h["holds"] == CASH)].iloc[-1]
        back = h[(executed >= span[-1]) & (h["holds"] == bond)]
        r_rule = float((1 + daily[rule].loc[span]).prod() - 1)
        r_held = float((1 + daily[own].loc[span]).prod() - 1)
        rows.append({"book": rule, "decided": into["decided"], "first_session": span[0].date().isoformat(),
                     "last_session": span[-1].date().isoformat(), "sessions": len(span),
                     "ends_with_switch_back": bool(len(back)) and pd.Timestamp(back["executed"].iloc[0]) == span[-1],
                     "rule_return": r_rule, "own_etf_held": own, "held_return": r_held,
                     "difference": r_rule - r_held})  # fmt: skip
    return pd.DataFrame(rows)


def decisions_frame(results, logs, carry) -> pd.DataFrame:
    """Each carry rule's decisions: the carry it read, what it held, when the harness traded it."""
    rows = []
    for name, log in logs.items():
        bond = RULES[name]
        h = holdings(results[name], bond).set_index("decided")
        for t, (c, holds) in sorted(log.items()):
            label = t.date().isoformat()
            if h.loc[label, "holds"] != holds:
                raise ValueError(f"{name}: the harness traded {h.loc[label, 'holds']} on {label}, the rule chose {holds}")
            rows.append({"book": name, "decided": label, "executed": h.loc[label, "executed"],
                         "segment_yield": float(carry.loc[t, f"{bond}_yield"]), "bill_yield": float(carry.loc[t, "bill_yield"]),
                         "carry": c, "holds": holds})  # fmt: skip
    return pd.DataFrame(rows)


def cost_runs(inp, main, days) -> pd.DataFrame:
    """The rules and their ETFs held at 0, 1 and 3 times the half-spreads and the impact."""
    _, _, tr_panel, tr_volumes, carry, _ = inp
    rows = []
    for name in (PRIMARY, *VARIANTS, TLT_HELD, IEI_HELD, SHY_HELD):
        for k in MULTIPLES:
            if k == 1:
                wf = main[name]
            else:
                strategy = carry_rule(RULES[name], carry, {}) if name in RULES else held(HELD[name])
                wf = run(tr_panel, tr_volumes, strategy, holdout=s1t.HOLDOUT_ROWS, costs=cost_model(k, R5_SPREADS))
            r = wf.equity["daily_return"].loc[days]
            m = s1t.measure(r, tr_panel)
            res = dated(wf.backtest["backtest_results"]).loc[days]
            total = float(res["daily_total_cost"].sum())
            rows.append({"book": name, "cost_multiple": k, "sharpe_excess": m["sharpe_excess"],
                         "annual_return": m["annual_return"], "annual_excess_return": m["annual_excess_return"],
                         "max_drawdown": m["max_drawdown"], "total_cost": total,
                         "cost_drag": total / (len(days) / TRADING_DAYS) / float(res["portfolio_value"].mean())})  # fmt: skip
    return pd.DataFrame(rows)


def series_frame(inp, days) -> pd.DataFrame:
    """The total-return closes every payer entered the harness with, beside its own closes and the
    usual basis's, on the days."""
    panel, _, tr_panel, _, _, payers = inp
    parts = {}
    for s in payers:
        parts[f"{s}_TR"] = tr_panel.loc[days, s]
        parts[f"{s}_close"] = data.master(s)["close"].reindex(days)
        parts[f"{s}_usual"] = panel.loc[days, s]
    return pd.DataFrame(parts)


# ── stages ────────────────────────────────────────────────────────────


ALL_BOOKS = (PRIMARY, *VARIANTS, TLT_HELD, IEI_HELD, SHY_HELD, S0_TR, S0_USUAL)
HOLDOUT_BOOKS = (PRIMARY, TLT_HELD, S0_TR)


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    inp = inputs()
    panel, volumes, tr_panel, tr_volumes, carry, payers = inp
    results, logs = books(inp, holdout=s1t.HOLDOUT_ROWS, names=ALL_BOOKS)
    daily = on_common_days(results)
    daily[MIX] = 0.5 * daily[S0_TR] + 0.5 * daily[PRIMARY]
    days = daily[S0_TR].index
    bases = {"tr": tr_panel, "usual": panel}

    trial_table = trials(daily, tr_panel)
    trial_table.to_csv(RESULTS / "trials.csv", index=False)
    trial_sharpes = trial_table["sharpe_excess"].tolist()
    variance = float(np.var(trial_sharpes, ddof=1))
    pd.DataFrame([{"n_trials": N_TRIALS, "sharpe_variance_annualized": variance,
                   "sharpe_variance_per_day": variance / TRADING_DAYS}]).to_csv(
        RESULTS / "deflated_sharpe_inputs.csv", index=False)  # fmt: skip

    table = book_rows(results, daily, bases, trial_sharpes=trial_sharpes)
    table.to_csv(RESULTS / "walkforward_books.csv", index=False)
    pd.DataFrame({**daily, "BIL_TR": tr_panel[CASH].pct_change(fill_method=None).reindex(days),
                  "BIL (usual basis)": panel[CASH].pct_change(fill_method=None).reindex(days)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip
    pd.DataFrame(daily).corr().rename_axis("book").to_csv(RESULTS / "walkforward_correlations.csv")
    before_holdout = panel.index[panel.index < panel.index[-s1t.HOLDOUT_ROWS]]
    series_frame(inp, before_holdout).to_csv(RESULTS / "walkforward_total_return_series.csv")
    decisions_frame(results, logs, carry).to_csv(RESULTS / "walkforward_decisions.csv", index=False)

    wins = windows(days[-1], holdout=False)
    stress = stress_table(daily, wins)
    stress.to_csv(RESULTS / "walkforward_stress.csv", index=False)
    eps = pd.concat([episodes(results, daily, rule) for rule in (PRIMARY, *VARIANTS)], ignore_index=True)
    eps.to_csv(RESULTS / "walkforward_episodes_in_bills.csv", index=False)
    boot = s1t.block_bootstrap(s1t.excess_returns(daily[PRIMARY], tr_panel))
    pd.DataFrame([{"book": PRIMARY, "percentile": p, "sharpe_excess": float(np.percentile(boot, p))}
                  for p in (5, 25, 50, 75, 95)]).to_csv(RESULTS / "walkforward_bootstrap.csv", index=False)  # fmt: skip
    costs = cost_runs(inp, results, days)
    costs.to_csv(RESULTS / "walkforward_cost_runs.csv", index=False)

    # each variant against its own ETF held, and the 50/50 book in the bond sell-offs (reported)
    t = table.set_index("book")
    pd.DataFrame([{"book": v, "own_etf_held": OWN_HELD[v], "sharpe_excess": t.loc[v, "sharpe_excess"],
                   "held_sharpe_excess": t.loc[OWN_HELD[v], "sharpe_excess"],
                   "annual_return": t.loc[v, "annual_return"], "held_annual_return": t.loc[OWN_HELD[v], "annual_return"],
                   "max_drawdown": t.loc[v, "max_drawdown"], "held_max_drawdown": t.loc[OWN_HELD[v], "max_drawdown"],
                   "worst_month": t.loc[v, "worst_month"], "held_worst_month": t.loc[OWN_HELD[v], "worst_month"]}
                  for v in (PRIMARY, *VARIANTS)]).to_csv(RESULTS / "walkforward_against_own_etf.csv", index=False)  # fmt: skip

    # the data and the harness: S0 on the usual basis with R1's costs must score R4's figure
    s0_usual = s1t.measure(daily[S0_USUAL], panel)["sharpe_excess"]
    r4_s0 = float(pd.read_csv(R4_BOOKS).set_index("book").loc["S0", "sharpe_excess"])
    same = lambda x, y: "equal" if abs(x - y) < 1e-9 else "DIFFERENT"  # noqa: E731
    by_calendar = [t for t in panel.index[(panel.index >= FIRST_DECISION) & (panel.index <= panel.index[-1])] if decision_day(t)]
    by_sessions = list(data.decision_dates())
    wf_logs = {name: sorted(log) for name, log in logs.items()}
    bil_tr = tr_panel[CASH].pct_change(fill_method=None).reindex(days)
    bil_usual = panel[CASH].pct_change(fill_method=None).reindex(days)
    yearly = pd.DataFrame({"tr": bil_tr, "usual": bil_usual}).groupby(days.year).apply(
        lambda g: (1 + g["tr"]).prod() - (1 + g["usual"]).prod())  # fmt: skip
    checks = [
        {"check": "panel", "value": f"{panel.index[0].date()} to {panel.index[-1].date()}, {len(panel)} sessions; "
                                    f"holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()}"},
        {"check": "walk-forward days measured (every book's common days)", "value": f"{days[0].date()} to {days[-1].date()}, {len(days)}"},
        {"check": "S0 on the usual basis with R1's costs, from its first trade: excess Sharpe here, and in R4's results",
         "value": f"{s0_usual:.6f} here, {r4_s0:.6f} in R4's, {same(s0_usual, r4_s0)}"},
        {"check": "on total-return closes (data.tr_closes), volumes rescaled by close / level", "value": ", ".join(payers)},
        {"check": "on S1's panel's closes (no distributions)", "value": ", ".join(NO_DISTRIBUTIONS)},
        {"check": "the total-return closes start at each ETF's close on its first date from 2005-01-03",
         "value": "; ".join(f"{s} {tr_panel[s].dropna().iloc[0]:.2f} = {data.master(s)['close'].loc[tr_panel[s].dropna().index[0]]:.2f}"
                            for s in ("TLT", "IEI", "SHY", "BIL"))},
        {"check": "the total-return level over the close on the walk-forward's last day",
         "value": "; ".join(f"{s} {tr_panel.loc[days[-1], s] / data.master(s)['close'].loc[days[-1]]:.3f}" for s in ("TLT", "IEI", "SHY", "BIL"))},
        {"check": "BIL_TR's return less the usual basis's BIL, each year (points)",
         "value": "; ".join(f"{y} {v * 100:+.2f}" for y, v in yearly.items() if abs(v) >= 0.00005)},
        {"check": "decision days from 2008-01-02 by the NYSE calendar (clock.py) against data.decision_dates() (the master's sessions)",
         "value": "the same" if by_calendar == [t for t in by_sessions if t <= panel.index[-1]] else "DIFFERENT"},
        {"check": "walk-forward decisions each carry rule made", "value": "; ".join(f"{n}: {len(v)}, {v[0].date()} to {v[-1].date()}"
                                                                                   for n, v in wf_logs.items())},
        {"check": "costs: half-spreads (bps) for the R5 books and S0_TR", "value": f"default 2.0; {R5_SPREADS}"},
        {"check": "costs: half-spreads (bps) for S0 on the usual basis (R1's, as R4's S0)", "value": f"default 2.0; {R1_SPREADS}"},
        {"check": "S0's target weights below 0 (a short would pay borrow)", "value": str(int((s1t.s0_targets()['target_weight'] < 0).sum()))},
        {"check": "the bond sell-offs data.sell_offs finds (FRED's 10-year from 2007-06-01)", "value": "HYPOTHESIS.md's nine and the holdout's"},
        {"check": "each window's return", "value": "from the close before its first date to the close of its last (or the walk-forward's end), "
                                                   "as in R3's and R4's tables"},
    ]  # fmt: skip
    pd.DataFrame(checks).to_csv(RESULTS / "data_checks.csv", index=False)

    p, tlt, s0, mix = t.loc[PRIMARY], t.loc[TLT_HELD], t.loc[S0_TR], t.loc[MIX]
    s = stress.set_index("window")
    r3 = s[s["kind"] == "R3's stress window"]
    worst_variant = min(t.loc[v, "sharpe_excess"] for v in VARIANTS)
    p5 = float(np.percentile(boot, 5))
    margin = float((s[PRIMARY] - s[TLT_HELD]).min())
    mix_margin = float((r3[MIX] - r3[S0_TR]).min())
    corr = float(daily[PRIMARY].corr(daily[S0_TR]))
    criteria = [("R5", PRIMARY, c, float(v), float(lim), bool(ok)) for c, v, lim, ok in [
        ("1. excess Sharpe >= 0.40", p["sharpe_excess"], 0.40, p["sharpe_excess"] >= 0.40),
        ("1b. excess Sharpe above TLT held's, the limit", p["sharpe_excess"], tlt["sharpe_excess"],
         p["sharpe_excess"] > tlt["sharpe_excess"]),
        ("2. deflated Sharpe >= 0.90 (n 36)", p["deflated_sharpe"], 0.90, p["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile of the excess Sharpe > 0", p5, 0.0, p5 > 0),
        ("4. every variant's excess Sharpe > 0 (worst variant)", worst_variant, 0.0, worst_variant > 0),
        ("5. max drawdown no deeper than 60% of TLT held's, the limit", p["max_drawdown"], 0.6 * tlt["max_drawdown"],
         p["max_drawdown"] >= 0.6 * tlt["max_drawdown"]),
        ("6. cost drag <= 1% a year", p["cost_drag"], 0.01, p["cost_drag"] <= 0.01),
        ("7. no day outside the mandate", p["mandate_breach_days"], 0.0, p["mandate_breach_days"] == 0),
        ("8. worst day no worse than -12%", p["worst_day"], -0.12, p["worst_day"] >= -0.12),
        ("9. worst calendar month no worse than TLT held's, the limit", p["worst_month"], tlt["worst_month"],
         p["worst_month"] >= tlt["worst_month"]),
        ("10. in each stress window no more than 0.25 points below TLT held's (smallest margin)", margin,
         -EPISODE_TOLERANCE, bool((s[PRIMARY] - s[TLT_HELD] >= -EPISODE_TOLERANCE).all())),
        ("11a. 50/50 S0_TR + R5's excess Sharpe > S0_TR's (difference)", mix["sharpe_excess"] - s0["sharpe_excess"], 0.0,
         mix["sharpe_excess"] > s0["sharpe_excess"]),
        ("11b. 50/50 S0_TR + R5 loses no more than S0_TR in each of R3's windows (smallest margin)", mix_margin, 0.0,
         bool((r3[MIX] >= r3[S0_TR]).all())),
        ("12. daily return correlation with S0_TR <= 0.50", corr, 0.50, corr <= 0.50),
    ]]  # fmt: skip
    pd.DataFrame(criteria, columns=["hypothesis", "book", "criterion", "value", "limit", "pass"]).to_csv(
        RESULTS / "walkforward_criteria.csv", index=False)  # fmt: skip
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    inp = inputs()
    panel, _, tr_panel, _, carry, _ = inp
    start = panel.index[-s1t.HOLDOUT_ROWS]
    results, logs = books(inp, holdout=0, names=HOLDOUT_BOOKS)
    daily = {k: v.loc[start:] for k, v in on_common_days(results).items()}
    daily[MIX] = 0.5 * daily[S0_TR] + 0.5 * daily[PRIMARY]
    days = daily[S0_TR].index

    table = book_rows(results, daily, {"tr": tr_panel, "usual": panel})
    table.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame({**daily, "BIL_TR": tr_panel[CASH].pct_change(fill_method=None).reindex(days)}).to_csv(
        RESULTS / "holdout_daily_returns.csv")  # fmt: skip
    series_frame(inp, days).to_csv(RESULTS / "holdout_total_return_series.csv")
    dec = decisions_frame(results, logs, carry)
    dec[pd.to_datetime(dec["decided"]) >= start - pd.Timedelta(days=31)].to_csv(RESULTS / "holdout_decisions.csv", index=False)
    stress_table(daily, windows(days[-1], holdout=True)).to_csv(RESULTS / "holdout_stress.csv", index=False)
    episodes(results, daily, PRIMARY).to_csv(RESULTS / "holdout_episodes_in_bills.csv", index=False)

    t = table.set_index("book")
    p, tlt = t.loc[PRIMARY], t.loc[TLT_HELD]
    killed = bool(p["sharpe_excess"] < -0.5 or p["max_drawdown"] < tlt["max_drawdown"] or p["worst_day"] < -0.12)
    pd.DataFrame([{"hypothesis": "R5", "book": PRIMARY,
                   "rule": "killed if its excess Sharpe is below -0.5, its drawdown deeper than TLT held's over the "
                           "holdout, or a day worse than -12%",
                   "sharpe_excess": p["sharpe_excess"], "max_drawdown": p["max_drawdown"],
                   "tlt_held_max_drawdown": tlt["max_drawdown"], "worst_day": p["worst_day"],
                   "killed": killed}]).to_csv(RESULTS / "holdout_criteria.csv", index=False)  # fmt: skip

    # the verdict, by HYPOTHESIS.md's rule: every criterion of the primary, and no holdout kill
    wf = pd.read_csv(RESULTS / "walkforward_criteria.csv")
    failed = wf.loc[~wf["pass"].astype(bool), "criterion"].tolist()
    pd.DataFrame([{"hypothesis": "R5", "criteria_passed": int(wf["pass"].astype(bool).sum()), "criteria": len(wf),
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
