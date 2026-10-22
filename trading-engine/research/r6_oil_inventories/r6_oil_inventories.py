"""
r6_oil_inventories.py — R6 through the research gate: USO at 37% of NAV while US crude stocks,
as EIA published them, are below their five-year norm, else BIL (the primary); the same on
Cushing's stocks (V1) and on days of supply (V2); and the reference books. HYPOTHESIS.md has
the rules, costs, split and criteria, amended at review before any run.

    python r6_oil_inventories.py                      # the walk-forward, then the holdout: every number
    python r6_oil_inventories.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python r6_oil_inventories.py --stage holdout      # the holdout

Use the repo's .venv Python. Reuses R5's runner (its total-return panel, its S0_TR and its cost
model), and through it S1's panel, harness and measures, so the numbers compare with the earlier
studies'. USO enters the C++ walk-forward on the master's split-adjusted closes and BIL as BIL_TR.
The gauges come from data.py, from EIA's figures cached in research/_data/eia/ (nothing is
downloaded once they are). Writes results/.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R5_DIR = HERE.parent / "r5_bond_carry"
sys.path.insert(0, str(HERE.parent / "s1_trend"))
sys.path.insert(0, str(HERE.parents[1]))  # trading-engine, for the NYSE calendar
import data  # noqa: E402
import s1_trend as s1t  # noqa: E402
from dv02_engine.walkforward import walk_forward  # noqa: E402


def _load_r5_runner():
    """R5's runner, with its own data module: both studies name theirs data.py, so R5's is put
    in place while its runner imports it, and this study's restored after."""
    mine = sys.modules.get("data")
    sys.modules["data"] = data.r5
    try:
        spec = importlib.util.spec_from_file_location("r5_bond_carry", R5_DIR / "r5_bond_carry.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.modules["data"] = mine
    return module


r5run = _load_r5_runner()

RESULTS = HERE / "results"
R5_TRIALS = R5_DIR / "results" / "trials.csv"
R5_BOOKS = R5_DIR / "results" / "walkforward_books.csv"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
FIRST_DECISION = pd.Timestamp(s1t.FIRST_DECISION)  # 2008-01-02
N_TRIALS = 39  # S1's 8 + S3a's 6 + R2's 9 + R3's 4 + R1's 3 + R4's 3 + R5's 3 + these 3
OIL = "USO"
SIZE = 0.37  # HYPOTHESIS.md, "Why 37%": the fund's 10% volatility target over USO's 26.9% in 2006-07
IN_OIL = {OIL: SIZE, CASH: round(BUDGET - SIZE, 10)}  # USO 37%, BIL 62.5%
OUT_OF_OIL = {CASH: BUDGET}  # BIL 99.5%
R6_SPREADS = {OIL: 10.0, CASH: 1.0}  # HYPOTHESIS.md's half-spreads, bps; every other ETF S1's 2 bps
COSTS = r5run.cost_model(1, R6_SPREADS)
MULTIPLES = (0, 1, 3)
EPISODE_TOLERANCE = 0.0025  # criterion 10: a window's return no more than 0.25 points below USO held's
DRAWDOWN_FLOOR = -0.25  # criterion 5, amended at review: and no deeper than -25%
RANDOM_DRAWS, RANDOM_SEED = 2_000, 21  # the random timings, added at review

PRIMARY = "R6 primary: USO while US stocks, as published, are below their norm"
V1 = "R6 V1: USO while Cushing's stocks are below their norm"
V2 = "R6 V2: USO while days of supply, as published, are below their norm"
VARIANTS = (V1, V2)
USO_HELD = "USO held at 37%"
S0_TR, S0_USUAL = r5run.S0_TR, r5run.S0_USUAL
MIX = "50/50 S0_TR + R6 primary"
GAUGE = {PRIMARY: "stocks", V1: "cushing", V2: "days_of_supply"}  # data.decisions()' columns
WINDOWS_R3 = {f"{name} ({a} to {b})": (a, b) for name, (a, b) in data.STRESS.items()}
# HYPOTHESIS.md's oil crashes, which data.crashes must find again
OIL_CRASHES = [("2008-09-22", "2008-12-23"), ("2013-09-06", "2015-03-17"), ("2015-10-09", "2016-01-20"),
               ("2018-06-27", "2018-12-27"), ("2019-04-23", "2020-03-30"), ("2020-04-03", "2020-04-20"),
               ("2022-03-08", "2023-03-17")]  # fmt: skip


# ── the inputs ────────────────────────────────────────────────────────


def inputs() -> dict:
    """R5's panels (S1's usual basis, and the total-return basis S0_TR stands on), and this
    study's: the total-return panel with USO on the master's own closes and volumes (S1's
    panel chains IBKR's adjusted closes in from 2016-10, and they carry a one-day blip on
    2017-03-20). And every rule's decisions, from data.py."""
    panel, volumes, tr_panel, tr_volumes, _, payers = r5run.inputs()
    uso = data.uso()
    listed = panel.index[panel[OIL].notna()]
    if not uso.index.intersection(panel.index).equals(listed):
        raise ValueError("USO's sessions in the master aren't the panel's")
    r6_panel, r6_volumes = tr_panel.copy(), tr_volumes.copy()
    r6_panel[OIL] = uso["close"].reindex(panel.index)
    r6_volumes[OIL] = uso["volume"].reindex(panel.index)
    return {"panel": panel, "volumes": volumes, "tr_panel": tr_panel, "tr_volumes": tr_volumes,
            "r6_panel": r6_panel, "r6_volumes": r6_volumes, "payers": payers, "decisions": data.decisions()}  # fmt: skip


# ── the books ─────────────────────────────────────────────────────────


def gauge_rule(name: str, decisions: pd.DataFrame, log: dict):
    """HYPOTHESIS.md's rule: on each decision day, USO at 37% and BIL at 62.5% while the gauge
    (the week it reads, against its norm) is below 0, else BIL at 99.5%. The gauge was read
    from a week that ended at least 7 days before the decision (data.known_week)."""
    gauge = decisions[GAUGE[name]]

    def strategy(history: pd.DataFrame):
        t = history.index[-1]
        if not r5run.decision_day(t):
            return None
        g = float(gauge.loc[t])
        holds = OIL if g < 0 else CASH
        log[t] = (g, holds)
        return dict(IN_OIL) if holds == OIL else dict(OUT_OF_OIL)

    return strategy


def held():
    """USO at 37% and BIL at 62.5%, re-sized on the same decision days."""

    def strategy(history: pd.DataFrame):
        return dict(IN_OIL) if r5run.decision_day(history.index[-1]) else None

    return strategy


def run(panel, volumes, strategy, *, holdout: int, costs=COSTS):
    """S1's harness as R5 ran it (lag 1, the mandate check, yearly test blocks); the strategies
    decide only on their decision days, so it is called on every row."""
    first = int(panel.index.searchsorted(FIRST_DECISION))
    return walk_forward(panel, strategy, train=first, test=s1t.TEST_ROWS, costs=costs, n_trials=N_TRIALS,
                        holdout=holdout, rebalance_every=1, lag=1, volumes=volumes, mandate=True)  # fmt: skip


def books(inp: dict, *, holdout: int, names) -> tuple[dict, dict]:
    """Each named book's walk-forward, and the rules' decision logs. S0_TR runs exactly as R5
    ran it (R5's panel and costs); S0 on the usual basis as R4's and R5's harness check."""
    logs, out = {}, {}
    for name in names:
        if name in GAUGE:
            logs[name] = {}
            out[name] = run(inp["r6_panel"], inp["r6_volumes"], gauge_rule(name, inp["decisions"], logs[name]), holdout=holdout)
        elif name == USO_HELD:
            out[name] = run(inp["r6_panel"], inp["r6_volumes"], held(), holdout=holdout)
        elif name == S0_TR:
            out[name] = run(inp["tr_panel"], inp["tr_volumes"], s1t.s0(s1t.s0_targets()), holdout=holdout, costs=r5run.COSTS)
        elif name == S0_USUAL:
            out[name] = run(inp["panel"], inp["volumes"], s1t.s0(s1t.s0_targets()), holdout=holdout, costs=r5run.R1_COSTS)
    return out, logs


# ── measuring ─────────────────────────────────────────────────────────

dated, worst_month, on_common_days = r5run.dated, r5run.worst_month, r5run.on_common_days


def holdings(wf) -> pd.DataFrame:
    """Each decision's holding, from the harness's own events: USO or BIL."""
    ev = wf.events.copy()
    w = ev[OIL].fillna(0.0) if OIL in ev.columns else pd.Series(0.0, index=ev.index)
    ev["holds"] = np.where(w > 0, OIL, CASH)
    return ev[["decided", "executed", "fold", "holds"]]


def in_oil(wf, days: pd.DatetimeIndex) -> pd.Series:
    """Whether each session's return was earned in USO: its position at the prior close."""
    pos = dated(wf.backtest["backtest_positions"])
    before = pos[OIL].shift(1).fillna(0.0) if OIL in pos.columns else pd.Series(0.0, index=pos.index)
    return (before > 0).reindex(days).fillna(False)


def book_rows(results, daily, bases: dict, *, trial_sharpes=None) -> pd.DataFrame:
    """One row a book. bases: the panel each book's excess return is over (BIL_TR for every
    book but S0 on the usual basis, which is over S1's BIL)."""
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
                       max_gross_exposure=wf.combined["max_gross_exposure"])  # fmt: skip
            if name in GAUGE or name == USO_HELD:
                h = holdings(wf)
                decided = pd.to_datetime(h["decided"])
                inside = h[(decided >= r.index[0] - pd.Timedelta(days=7)) & (decided <= r.index[-1])]
                row.update(decisions=len(inside), decisions_in_oil=int((inside["holds"] == OIL).sum()),
                           decisions_in_bills=int((inside["holds"] == CASH).sum()),
                           sessions_in_oil=int(in_oil(wf, r.index).sum()), sessions=len(r),
                           switches=int((inside["holds"] != inside["holds"].shift(1)).iloc[1:].sum()))  # fmt: skip
        row["correlation_with_s0_tr"] = float(r.corr(daily[S0_TR]))
        row["correlation_with_uso_held"] = float(r.corr(daily[USO_HELD]))
        rows.append(row)
    table = pd.DataFrame(rows)
    # the 50/50 book: half of each half's costs (as the earlier studies'), both halves' mandate records
    t = table.set_index("book")
    if MIX in t.index:
        for k in ("cost_drag", "total_cost", "commission_cost", "spread_cost", "impact_cost"):
            table.loc[table["book"] == MIX, k] = 0.5 * t.loc[S0_TR, k] + 0.5 * t.loc[PRIMARY, k]
        table.loc[table["book"] == MIX, "mandate_breach_days"] = (
            t.loc[S0_TR, "mandate_breach_days"] + t.loc[PRIMARY, "mandate_breach_days"])  # fmt: skip
    return table


def trials(daily, panel) -> pd.DataFrame:
    """The 39 trials: R5's file lists the first 36; these 3 from this run."""
    earlier = pd.read_csv(R5_TRIALS)
    ours = pd.DataFrame([{"study": "R6", "trial": n, "sharpe_excess": s1t.measure(daily[n], panel)["sharpe_excess"]}
                         for n in (PRIMARY, *VARIANTS)])  # fmt: skip
    out = pd.concat([earlier, ours], ignore_index=True)
    assert len(out) == N_TRIALS, len(out)
    return out


def windows(last: pd.Timestamp) -> list[dict]:
    """The stress windows: R3's eight and oil's crashes (data.crashes, which must find
    HYPOTHESIS.md's list), each judged to ``last`` at the latest."""
    found = [(a.date().isoformat(), b.date().isoformat()) for a, b, _ in data.crashes() if b >= FIRST_DECISION]
    if found != OIL_CRASHES:
        raise ValueError(f"data.crashes found {found}, not HYPOTHESIS.md's {OIL_CRASHES}")
    spot = data.weekly("spot")
    out = [{"window": label, "kind": "R3's stress window", "start": a, "end": b, "judged_to": b} for label, (a, b) in WINDOWS_R3.items()]
    for a, b in OIL_CRASHES:
        fall = max(spot.loc[b], data.SPOT_FLOOR) / spot.loc[a] - 1
        out.append({"window": f"oil crash {a} to {b} ({fall:+.0%})", "kind": "oil crash", "start": a, "end": b,
                    "judged_to": min(pd.Timestamp(b), last).date().isoformat()})  # fmt: skip
    return out


def stress_table(daily: dict[str, pd.Series], wins: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{**w, **{name: s1t.period_return(r, w["start"], w["judged_to"]) for name, r in daily.items()}}
                         for w in wins])  # fmt: skip


def episodes(results, daily, rule: str) -> pd.DataFrame:
    """Each run of sessions whose return the rule earned in USO (held at the prior close),
    against USO held at 37% over the same sessions, and USO's own return. A run's first session
    is the one after its switch into oil filled; its switch back filled on the run's last
    session, so that switch's cost is inside and the switch in's isn't."""
    days = daily[rule].index
    flag = in_oil(results[rule], days)
    run_id = (flag != flag.shift(1)).cumsum()[flag]
    uso = data.uso()["close"].pct_change().reindex(days)
    rows = []
    for _, span in run_id.groupby(run_id):
        span = span.index
        grow = lambda r: float((1 + r.loc[span]).prod() - 1)  # noqa: E731
        rows.append({"book": rule, "first_session": span[0].date().isoformat(), "last_session": span[-1].date().isoformat(),
                     "sessions": len(span), "rule_return": grow(daily[rule]), "uso_held_return": grow(daily[USO_HELD]),
                     "uso_return": grow(uso), "rule_max_drawdown_in_run": _drawdown(daily[rule].loc[span])})  # fmt: skip
    return pd.DataFrame(rows)


def _drawdown(r: pd.Series) -> float:
    g = (1 + r).cumprod()
    return float((g / g.cummax().clip(lower=1.0) - 1).min())


def decisions_frame(results, logs, decisions: pd.DataFrame) -> pd.DataFrame:
    """Each rule's decisions: the week it read, the gauge, what it held, when the harness traded."""
    rows = []
    for name, log in logs.items():
        h = holdings(results[name]).set_index("decided")
        g = GAUGE[name]
        for t, (gauge, holds) in sorted(log.items()):
            label = t.date().isoformat()
            if h.loc[label, "holds"] != holds:
                raise ValueError(f"{name}: the harness traded {h.loc[label, 'holds']} on {label}, the rule chose {holds}")
            rows.append({"book": name, "decided": label, "executed": h.loc[label, "executed"],
                         "week_read": decisions.loc[t, f"{g}_week"].date().isoformat(), "gauge": gauge, "holds": holds})  # fmt: skip
    return pd.DataFrame(rows)


def cost_runs(inp: dict, main: dict, days) -> pd.DataFrame:
    """The rules and USO held at 0, 1 and 3 times the half-spreads and the impact."""
    rows = []
    for name in (PRIMARY, *VARIANTS, USO_HELD):
        for k in MULTIPLES:
            if k == 1:
                wf = main[name]
            else:
                strategy = gauge_rule(name, inp["decisions"], {}) if name in GAUGE else held()
                wf = run(inp["r6_panel"], inp["r6_volumes"], strategy, holdout=s1t.HOLDOUT_ROWS,
                         costs=r5run.cost_model(k, R6_SPREADS))  # fmt: skip
            r = wf.equity["daily_return"].loc[days]
            m = s1t.measure(r, inp["tr_panel"])
            res = dated(wf.backtest["backtest_results"]).loc[days]
            total = float(res["daily_total_cost"].sum())
            rows.append({"book": name, "cost_multiple": k, "sharpe_excess": m["sharpe_excess"],
                         "annual_return": m["annual_return"], "annual_excess_return": m["annual_excess_return"],
                         "max_drawdown": m["max_drawdown"], "total_cost": total,
                         "cost_drag": total / (len(days) / TRADING_DAYS) / float(res["portfolio_value"].mean())})  # fmt: skip
    return pd.DataFrame(rows)


def random_timing(inp: dict, primary_wf, days: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """HYPOTHESIS.md's random timings (added at review): 2,000 draws (seed 21), each in oil at
    37% on as many of the walk-forward's decisions as the primary, chosen at random, held over
    the same sessions the primary's positions are (bought at the close each decision traded,
    drifting with the prices to the next), from the daily returns of USO and BIL_TR before
    costs. The primary goes through the same arithmetic, so the comparison is like for like."""
    panel = inp["r6_panel"]
    r_uso = panel[OIL].pct_change(fill_method=None).reindex(days).fillna(0.0).to_numpy()
    r_bil = panel[CASH].pct_change(fill_method=None).reindex(days).fillna(0.0).to_numpy()
    h = holdings(primary_wf)
    h = h[pd.to_datetime(h["decided"]) <= days[-1]]
    executed = pd.to_datetime(h["executed"]).to_numpy()
    # each session's period: the latest trade filled before it (-1 before the first trade: all cash)
    period = np.searchsorted(executed, days.to_numpy(), side="left") - 1
    weights = {True: (SIZE, IN_OIL[CASH]), False: (0.0, BUDGET)}
    paths = {}
    for oil in (True, False):
        w_uso, w_bil = weights[oil]
        out = np.zeros(len(days))
        for p in range(len(executed)):
            idx = np.flatnonzero(period == p)
            if not len(idx):
                continue
            g_uso, g_bil = np.cumprod(1 + r_uso[idx]), np.cumprod(1 + r_bil[idx])
            value = w_uso * g_uso + w_bil * g_bil + (1 - BUDGET)
            prev = np.concatenate([[1.0], value[:-1]])
            out[idx] = value / prev - 1
        paths[oil] = out

    def sharpe(in_oil_by_period: np.ndarray) -> float:
        flag = np.where(period >= 0, in_oil_by_period[np.clip(period, 0, None)], False)
        r = np.where(flag, paths[True], paths[False]) - r_bil
        return float(r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS))

    primary_flags = (h["holds"] == OIL).to_numpy()
    n_in = int(primary_flags.sum())
    rng = np.random.default_rng(RANDOM_SEED)
    draws = np.empty(RANDOM_DRAWS)
    for i in range(RANDOM_DRAWS):
        flags = np.zeros(len(primary_flags), dtype=bool)
        flags[rng.choice(len(primary_flags), size=n_in, replace=False)] = True
        draws[i] = sharpe(flags)
    primary = sharpe(primary_flags)
    summary = pd.DataFrame([{"draws": RANDOM_DRAWS, "seed": RANDOM_SEED, "decisions": len(primary_flags),
                             "decisions_in_oil": n_in, "primary_sharpe_excess_before_costs": primary,
                             "share_of_draws_below_the_primary": float((draws < primary).mean()),
                             "draws_p5": float(np.percentile(draws, 5)), "draws_p25": float(np.percentile(draws, 25)),
                             "draws_median": float(np.median(draws)), "draws_p75": float(np.percentile(draws, 75)),
                             "draws_p95": float(np.percentile(draws, 95)), "draws_mean": float(draws.mean())}])  # fmt: skip
    return summary, pd.DataFrame({"draw": np.arange(RANDOM_DRAWS), "sharpe_excess": draws})


# ── stages ────────────────────────────────────────────────────────────


ALL_BOOKS = (PRIMARY, *VARIANTS, USO_HELD, S0_TR, S0_USUAL)
HOLDOUT_BOOKS = (PRIMARY, USO_HELD, S0_TR)


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    inp = inputs()
    panel, tr_panel, dec = inp["panel"], inp["tr_panel"], inp["decisions"]
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
                  "USO": inp["r6_panel"][OIL].pct_change(fill_method=None).reindex(days)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip
    pd.DataFrame(daily).corr().rename_axis("book").to_csv(RESULTS / "walkforward_correlations.csv")
    decisions_frame(results, logs, dec).to_csv(RESULTS / "walkforward_decisions.csv", index=False)

    stress = stress_table(daily, windows(days[-1]))
    stress.to_csv(RESULTS / "walkforward_stress.csv", index=False)
    pd.concat([episodes(results, daily, rule) for rule in (PRIMARY, *VARIANTS)], ignore_index=True).to_csv(
        RESULTS / "walkforward_episodes_in_oil.csv", index=False)  # fmt: skip
    boot = s1t.block_bootstrap(s1t.excess_returns(daily[PRIMARY], tr_panel))
    pd.DataFrame([{"book": PRIMARY, "percentile": p, "sharpe_excess": float(np.percentile(boot, p))}
                  for p in (5, 25, 50, 75, 95)]).to_csv(RESULTS / "walkforward_bootstrap.csv", index=False)  # fmt: skip
    costs = cost_runs(inp, results, days)
    costs.to_csv(RESULTS / "walkforward_cost_runs.csv", index=False)
    summary, draws = random_timing(inp, results[PRIMARY], days)
    summary.to_csv(RESULTS / "walkforward_random_timing.csv", index=False)
    draws.to_csv(RESULTS / "walkforward_random_timing_draws.csv", index=False)

    # the harness: S0 on the usual basis with R1's costs must score R4's and R5's figure, S0_TR R5's
    t = table.set_index("book")
    r5_books = pd.read_csv(R5_BOOKS).set_index("book")
    same = lambda x, y: "equal" if abs(x - y) < 1e-9 else "DIFFERENT"  # noqa: E731
    uso_master = data.uso()["close"]
    chained = panel[OIL].dropna()
    blip = (chained.pct_change() - uso_master.reindex(chained.index).pct_change()).abs()
    checks = [
        {"check": "panel", "value": f"{panel.index[0].date()} to {panel.index[-1].date()}, {len(panel)} sessions; "
                                    f"holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()}"},
        {"check": "walk-forward days measured (every book's common days)", "value": f"{days[0].date()} to {days[-1].date()}, {len(days)}"},
        {"check": "S0 on the usual basis with R1's costs: excess Sharpe here, and in R5's results",
         "value": f"{t.loc[S0_USUAL, 'sharpe_excess']:.6f} here, {r5_books.loc[S0_USUAL, 'sharpe_excess']:.6f} in R5's, "
                  f"{same(t.loc[S0_USUAL, 'sharpe_excess'], r5_books.loc[S0_USUAL, 'sharpe_excess'])}"},
        {"check": "S0_TR with R5's panel and costs: excess Sharpe here, and in R5's results",
         "value": f"{t.loc[S0_TR, 'sharpe_excess']:.6f} here, {r5_books.loc[S0_TR, 'sharpe_excess']:.6f} in R5's, "
                  f"{same(t.loc[S0_TR, 'sharpe_excess'], r5_books.loc[S0_TR, 'sharpe_excess'])}"},
        {"check": "USO in this study's panel: the master's closes; S1's panel chains IBKR's adjusted closes, whose daily "
                  "returns differ from the master's on", "value": ", ".join(f"{d.date()} ({x * 1e4:.1f} bps)" for d, x in blip[blip > 1e-9].items())},
        {"check": "decisions each rule made in the walk-forward",
         "value": "; ".join(f"{n}: {len(v)}, {min(v).date()} to {max(v).date()}" for n, v in logs.items())},
        {"check": "the decisions' days against data.decision_dates()",
         "value": "the same" if all(sorted(v) == [d for d in dec.index if d <= max(v)] for v in logs.values()) else "DIFFERENT"},
        {"check": "the weeks read: each ended at least 7 days before its decision",
         "value": str(bool(all((t_ - dec.loc[t_, f"{g}_week"]).days >= data.LAG_DAYS for t_ in dec.index for g in GAUGE.values())))},
        {"check": "costs: half-spreads (bps) for the R6 books", "value": f"default 2.0; {R6_SPREADS}"},
        {"check": "costs: S0_TR as R5's, S0 on the usual basis as R1's", "value": f"{r5run.R5_SPREADS}; {r5run.R1_SPREADS}"},
        {"check": "oil's crashes data.crashes finds (WTI spot from 2008)", "value": "HYPOTHESIS.md's seven"},
        {"check": "each window's return", "value": "from the close before its first date to the close of its last, as in "
                                                   "R3's, R4's and R5's tables"},
    ]  # fmt: skip
    pd.DataFrame(checks).to_csv(RESULTS / "data_checks.csv", index=False)

    p, held_, s0, mix = t.loc[PRIMARY], t.loc[USO_HELD], t.loc[S0_TR], t.loc[MIX]
    s = stress.set_index("window")
    r3 = s[s["kind"] == "R3's stress window"]
    worst_variant = min(t.loc[v, "sharpe_excess"] for v in VARIANTS)
    p5 = float(np.percentile(boot, 5))
    margin = float((s[PRIMARY] - s[USO_HELD]).min())
    mix_margin = float((r3[MIX] - r3[S0_TR]).min())
    corr = float(daily[PRIMARY].corr(daily[S0_TR]))
    dd_limit = max(0.6 * held_["max_drawdown"], DRAWDOWN_FLOOR)
    criteria = [("R6", PRIMARY, c, float(v), float(lim), bool(ok)) for c, v, lim, ok in [
        ("1. excess Sharpe >= 0.40", p["sharpe_excess"], 0.40, p["sharpe_excess"] >= 0.40),
        ("1b. excess Sharpe above USO held's, the limit", p["sharpe_excess"], held_["sharpe_excess"],
         p["sharpe_excess"] > held_["sharpe_excess"]),
        ("2. deflated Sharpe >= 0.90 (n 39)", p["deflated_sharpe"], 0.90, p["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile of the excess Sharpe > 0", p5, 0.0, p5 > 0),
        ("4. every variant's excess Sharpe > 0 (worst variant)", worst_variant, 0.0, worst_variant > 0),
        ("5. max drawdown no deeper than 60% of USO held's and no deeper than -25%, the tighter limit", p["max_drawdown"],
         dd_limit, p["max_drawdown"] >= dd_limit),
        ("6. cost drag <= 1% a year", p["cost_drag"], 0.01, p["cost_drag"] <= 0.01),
        ("7. no day outside the mandate", p["mandate_breach_days"], 0.0, p["mandate_breach_days"] == 0),
        ("8. worst day no worse than -12%", p["worst_day"], -0.12, p["worst_day"] >= -0.12),
        ("9. worst calendar month no worse than USO held's, the limit", p["worst_month"], held_["worst_month"],
         p["worst_month"] >= held_["worst_month"]),
        ("10. in each stress window no more than 0.25 points below USO held's (smallest margin)", margin,
         -EPISODE_TOLERANCE, bool((s[PRIMARY] - s[USO_HELD] >= -EPISODE_TOLERANCE).all())),
        ("11a. 50/50 S0_TR + R6's excess Sharpe > S0_TR's (difference)", mix["sharpe_excess"] - s0["sharpe_excess"], 0.0,
         mix["sharpe_excess"] > s0["sharpe_excess"]),
        ("11b. 50/50 S0_TR + R6 loses no more than S0_TR in each of R3's windows (smallest margin)", mix_margin, 0.0,
         bool((r3[MIX] >= r3[S0_TR]).all())),
        ("12. daily return correlation with S0_TR <= 0.50", corr, 0.50, corr <= 0.50),
    ]]  # fmt: skip
    pd.DataFrame(criteria, columns=["hypothesis", "book", "criterion", "value", "limit", "pass"]).to_csv(
        RESULTS / "walkforward_criteria.csv", index=False)  # fmt: skip
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    inp = inputs()
    panel, tr_panel, dec = inp["panel"], inp["tr_panel"], inp["decisions"]
    start = panel.index[-s1t.HOLDOUT_ROWS]
    results, logs = books(inp, holdout=0, names=HOLDOUT_BOOKS)
    daily = {k: v.loc[start:] for k, v in on_common_days(results).items()}
    daily[MIX] = 0.5 * daily[S0_TR] + 0.5 * daily[PRIMARY]
    days = daily[S0_TR].index

    table = book_rows(results, daily, {"tr": tr_panel, "usual": panel})
    table.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame({**daily, "BIL_TR": tr_panel[CASH].pct_change(fill_method=None).reindex(days),
                  "USO": inp["r6_panel"][OIL].pct_change(fill_method=None).reindex(days)}).to_csv(
        RESULTS / "holdout_daily_returns.csv")  # fmt: skip
    d = decisions_frame(results, logs, dec)
    d[pd.to_datetime(d["decided"]) >= start - pd.Timedelta(days=31)].to_csv(RESULTS / "holdout_decisions.csv", index=False)
    episodes(results, daily, PRIMARY).to_csv(RESULTS / "holdout_episodes_in_oil.csv", index=False)

    t = table.set_index("book")
    p, held_ = t.loc[PRIMARY], t.loc[USO_HELD]
    killed = bool(p["sharpe_excess"] < -0.5 or p["max_drawdown"] < held_["max_drawdown"] or p["worst_day"] < -0.12)
    pd.DataFrame([{"hypothesis": "R6", "book": PRIMARY,
                   "rule": "killed if its excess Sharpe is below -0.5, its drawdown deeper than USO held's over the "
                           "holdout, or a day worse than -12%",
                   "sharpe_excess": p["sharpe_excess"], "max_drawdown": p["max_drawdown"],
                   "uso_held_max_drawdown": held_["max_drawdown"], "worst_day": p["worst_day"],
                   "killed": killed}]).to_csv(RESULTS / "holdout_criteria.csv", index=False)  # fmt: skip

    # the verdict, by HYPOTHESIS.md's rule: every criterion of the primary, and no holdout kill
    wf = pd.read_csv(RESULTS / "walkforward_criteria.csv")
    failed = wf.loc[~wf["pass"].astype(bool), "criterion"].tolist()
    pd.DataFrame([{"hypothesis": "R6", "criteria_passed": int(wf["pass"].astype(bool).sum()), "criteria": len(wf),
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
