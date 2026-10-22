"""
r3_vix_futures.py — R3 through the research gate: short the VIX futures premium (VXCM, the
30-day constant-maturity VX roll) while the curve is in contango, its three variants, and the
reference books. HYPOTHESIS.md has the rules, costs, split and criteria; REPORT.md the verdict.

    python r3_vix_futures.py                      # the walk-forward, then the holdout: every number
    python r3_vix_futures.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python r3_vix_futures.py --stage holdout      # the holdout

Use the repo's .venv Python. Reuses research/s1_trend's panel, harness and measures, so the
numbers compare with S1's, S3a's and R2's. Reads the VX and VIX files data.py cached in
research/_data/ (it downloads them only if they're missing) and writes results/.
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
import data  # noqa: E402
import s1_trend as s1t  # noqa: E402
from dv02_engine.costs import CostModel, FuturesRoot, future  # noqa: E402
from dv02_engine.walkforward import walk_forward  # noqa: E402

RESULTS = HERE / "results"
R2_TRIALS = HERE.parent / "r2_vol_positioning" / "results" / "trials.csv"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
VXCM = "VXCM"
PANEL_END = "2026-10-16"  # R2's panel: the holdout is its last 756 sessions, from 2023-10-12
FIRST_DECISION = s1t.FIRST_DECISION  # 2008-01-02
NOTIONAL = 0.10  # the short, a share of NAV
V1_RATIO = 1.05  # V1: CM30 / VIX at least this
V2_TARGET, V2_WINDOW = 0.05, 21  # V2: notional = min(10%, 5% / sigma over 21 sessions)
V3_WINDOW, V3_CAP = 63, 0.40  # V3: hedge = |beta over 63 sessions| x notional, at most 40%
N_TRIALS = 27  # S1's 8 + S3a's 6 + R2's 9 + these 4
MANDATE_TOLERANCE = 1e-9

# HYPOTHESIS.md's costs: VXCM is a future (no borrow, moves no cash) at 6 bps a side and 8 bps of
# notional a month for the roll; BIL and SPY take S1's CostModel
COSTS = CostModel(
    half_spread_bps=2.0,
    half_spread_bps_by_symbol={VXCM: 6.0},
    impact_k=0.5,
    borrow_bps=25.0,
    borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
    cash_interest_bps=0.0,  # as S1's: idle money earns nothing
    instruments={VXCM: future(1.0, VXCM)},
    futures={VXCM: FuturesRoot(commission=0.0, roll_bps=8.0, roll_months=list(range(1, 13)), roll_day=1)},
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
PRIMARY = "R3 primary: short 10% in contango"
V1 = "R3 V1: short 10% while CM30/VIX >= 1.05"
V2 = "R3 V2: volatility-scaled size"
V3 = "R3 V3: equity-hedged with SPY"
VARIANTS = (V1, V2, V3)
ALWAYS = "always short 10% (reference)"
S0_NAME, S1_NAME = "S0", "S1 blend long/flat"
MIX = "50/50 S0 + R3 primary"


# ── data ──────────────────────────────────────────────────────────────


def inputs():
    """(panel with VXCM, volumes, the 30-day series, VIX). The panel ends at PANEL_END; a session
    without a VX settlement carries VXCM's last level, and a CFE day that isn't a session folds
    its move into the next session's (a level, not a return, is what the panel holds)."""
    if not any(data.CURRENT_DIR.glob("VX_*.csv")) or not data.VIX_PATH.exists():
        data.fetch()
    panel, volumes, _ = s1t.load_panel()
    panel, volumes = panel.loc[:PANEL_END], volumes.loc[:PANEL_END]
    cm = data.cm30()
    level = cm["er_level"].reindex(cm.index.union(panel.index)).ffill().reindex(panel.index)
    return panel.assign(**{VXCM: level}), volumes.assign(**{VXCM: np.nan}), cm, data.vix()


def latest(series: pd.Series, t: pd.Timestamp) -> float:
    """The latest value on or before t."""
    known = series.loc[:t]
    if known.empty:
        raise ValueError(f"no value by {t.date()}")
    return float(known.iloc[-1])


# ── the rules (HYPOTHESIS.md) ─────────────────────────────────────────


def contango(cm: pd.DataFrame, vix: pd.Series, t: pd.Timestamp, ratio: float | None = None) -> bool:
    """The primary's condition, CM30 > VIX at the close of t; with `ratio`, CM30 / VIX >= ratio."""
    price, spot = latest(cm["cm30_price"], t), latest(vix, t)
    return price / spot >= ratio if ratio is not None else price > spot


def short_book(notional: float, hedge: float = 0.0) -> dict[str, float]:
    book = {VXCM: -notional, CASH: BUDGET - notional - hedge}
    if hedge > 0:
        book["SPY"] = -hedge
    return book


def primary(cm, vix, ratio: float | None = None):
    """Short VXCM at 10% of NAV while in contango (or CM30 / VIX >= ratio), else flat in BIL;
    re-sized at every close."""

    def strategy(history: pd.DataFrame):
        return short_book(NOTIONAL) if contango(cm, vix, history.index[-1], ratio) else {CASH: BUDGET}

    return strategy


def volatility(history: pd.DataFrame, window: int) -> float:
    """VXCM's annualized standard deviation (ddof 1) over the last `window` sessions up to t."""
    r = history[VXCM].iloc[-(window + 1) :].pct_change(fill_method=None).iloc[1:]
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def scaled(cm, vix):
    """V2: the primary's condition, notional min(10%, 5% / sigma)."""

    def strategy(history: pd.DataFrame):
        if not contango(cm, vix, history.index[-1]):
            return {CASH: BUDGET}
        sigma = volatility(history, V2_WINDOW)
        notional = min(NOTIONAL, V2_TARGET / sigma) if sigma > 0 else NOTIONAL
        return short_book(notional)

    return strategy


def beta(history: pd.DataFrame, window: int) -> float:
    """The OLS slope of VXCM's daily returns on SPY's over the last `window` sessions up to t."""
    r = history[[VXCM, "SPY"]].iloc[-(window + 1) :].pct_change(fill_method=None).iloc[1:].to_numpy()
    x, y = r[:, 0], r[:, 1]
    return float(np.cov(x, y, ddof=1)[0, 1] / np.var(y, ddof=1))


def hedged(cm, vix):
    """V3: the primary plus a short SPY hedge of |beta| x the notional, capped at 40%; no hedge
    when beta >= 0."""

    def strategy(history: pd.DataFrame):
        if not contango(cm, vix, history.index[-1]):
            return {CASH: BUDGET}
        b = beta(history, V3_WINDOW)
        return short_book(NOTIONAL, min(V3_CAP, abs(b) * NOTIONAL) if b < 0 else 0.0)

    return strategy


def always_short():
    def strategy(history: pd.DataFrame):
        return short_book(NOTIONAL)

    return strategy


# ── running and measuring ─────────────────────────────────────────────


def run(panel, volumes, strategy, *, rebalance_every: int, holdout: int):
    """S1's harness (lag 1, the mandate check, yearly test blocks) with this study's costs."""
    first = int(panel.index.searchsorted(pd.Timestamp(FIRST_DECISION)))
    return walk_forward(panel, strategy, train=first, test=s1t.TEST_ROWS, costs=COSTS, n_trials=N_TRIALS,
                        holdout=holdout, rebalance_every=rebalance_every, lag=1, volumes=volumes,
                        mandate=True)  # fmt: skip


def books(panel, volumes, cm, vix, *, holdout: int, variants: bool = True) -> dict[str, object]:
    targets = s1t.s0_targets()
    plan = {PRIMARY: (primary(cm, vix), 1), ALWAYS: (always_short(), 1), S0_NAME: (s1t.s0(targets), 1),
            S1_NAME: (s1t.s1("blend", "long_flat"), s1t.REBALANCE_EVERY)}  # fmt: skip
    if variants:
        plan.update({V1: (primary(cm, vix, V1_RATIO), 1), V2: (scaled(cm, vix), 1), V3: (hedged(cm, vix), 1)})
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


def pre_trade_gross(result, panel: pd.DataFrame) -> pd.DataFrame:
    """Each close's gross before that close's re-sizing: the previous close's positions moved
    by the day's prices, over the value they'd make with the previous close's cash (the day's
    roll and borrow, paid before the trades, left out). Information only: the mandate is judged
    after each close's trades (HYPOTHESIS.md)."""
    pos = result.backtest["backtest_positions"].copy()
    pos.index = pd.to_datetime(pos.pop("date"))
    assets = [c for c in pos.columns if c != "cash"]
    prices = panel[assets].reindex(pos.index)
    drifted = pos[assets].shift(1).to_numpy() * (prices / prices.shift(1)).to_numpy()
    cash = pos["cash"].shift(1).to_numpy()
    value = cash + np.nansum(drifted, axis=1)
    futures = np.nan_to_num(drifted[:, assets.index(VXCM)]) if VXCM in assets else np.zeros(len(value))
    out = pd.DataFrame({"gross_before_trades": np.nansum(np.abs(drifted), axis=1) / value,
                        "cash_before_trades": (cash + futures) / value}, index=pos.index)  # fmt: skip
    return out.iloc[1:]


def drift_rows(results: dict[str, object], panel: pd.DataFrame, since: pd.Timestamp | None = None) -> pd.DataFrame:
    """For each book that shorts VXCM: the closes whose gross was over 100% before the re-sizing."""
    rows = []
    for name, wf in results.items():
        if name in (S0_NAME, S1_NAME):
            continue
        d = pre_trade_gross(wf, panel)
        if since is not None:
            d = d.loc[since:]
        over = d[d["gross_before_trades"] > 1.0 + MANDATE_TOLERANCE]
        neg = d[d["cash_before_trades"] < -MANDATE_TOLERANCE]
        rows.append({"book": name, "closes": len(d), "closes_over_100pct_gross_before_trades": len(over),
                     "max_gross_before_trades": float(d["gross_before_trades"].max()),
                     "date_of_max": d["gross_before_trades"].idxmax().date().isoformat(),
                     "closes_cash_below_0_before_trades": len(neg),
                     "min_cash_before_trades": float(d["cash_before_trades"].min()),
                     "dates_over_100pct": "; ".join(f"{t.date()} {g:.4f}" for t, g in over["gross_before_trades"].items())})  # fmt: skip
    return pd.DataFrame(rows)


def short_share(events: pd.DataFrame) -> dict[str, float]:
    vx = events[VXCM].fillna(0.0) if VXCM in events.columns else pd.Series(0.0, index=events.index)
    spy = events["SPY"].fillna(0.0) if "SPY" in events.columns else pd.Series(0.0, index=events.index)
    short = vx < 0
    return {
        "share_of_decisions_short": float(short.mean()),
        "avg_vx_notional_when_short": float(-vx[short].mean()) if short.any() else 0.0,
        "avg_spy_hedge_when_short": float(-spy[short].mean()) if short.any() else 0.0,
        "entries": int((short & ~short.shift(1, fill_value=False)).sum()),
    }


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
                       spread_cost=c["spread_cost"], roll_cost=c["roll_cost"], commission_cost=c["commission_cost"],
                       borrow_cost=c["borrow_cost"], impact_cost=c["impact_cost"],
                       unmeasured_impact_trades=float(metrics.get("unmeasured_impact_trades", math.nan)),
                       mandate_breach_days=c["mandate_breach_days"], max_gross_exposure=c["max_gross_exposure"],
                       decisions=len(events))  # fmt: skip
            if name not in (S0_NAME, S1_NAME):
                row.update(short_share(events))
        row.update(deflated_sharpe=dsr, deflated_sharpe_threshold=threshold)
        row["correlation_with_s0"] = float(r.corr(daily[S0_NAME]))
        row["correlation_with_s1_blend_long_flat"] = float(r.corr(daily[S1_NAME]))
        rows.append(row)
    return pd.DataFrame(rows)


def mix_record(c0: dict, c1: dict) -> dict[str, float]:
    """A 50/50 book's turnover, cost drag and mandate record, as S3a's and R2's: half of each sleeve's."""
    return {
        "turnover": 0.5 * c0["turnover"] + 0.5 * c1["turnover"],
        "cost_drag": 0.5 * c0["cost_drag"] + 0.5 * c1["cost_drag"],
        "mandate_breach_days": c0["mandate_breach_days"] + c1["mandate_breach_days"],
    }


def trials(daily, panel) -> pd.DataFrame:
    """The 27 trials: R2's file lists the first 23 (S1's 8, S3a's 6, R2's 9); these 4 from this run."""
    earlier = pd.read_csv(R2_TRIALS)
    ours = pd.DataFrame([{"study": "R3", "trial": n, "sharpe_excess": s1t.measure(daily[n], panel)["sharpe_excess"]}
                         for n in (PRIMARY, *VARIANTS)])  # fmt: skip
    out = pd.concat([earlier, ours], ignore_index=True)
    assert len(out) == N_TRIALS, len(out)
    return out


def decisions(events: pd.DataFrame, cm: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    """The primary's decisions with the signal each one read."""
    rows = []
    for t in pd.to_datetime(events["decided"]):
        price, spot = latest(cm["cm30_price"], t), latest(vix, t)
        rows.append({"cm30_price": price, "vix": spot, "cm30_over_vix": price / spot, "contango": price > spot})
    return pd.concat([events.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def coverage(panel: pd.DataFrame, cm: pd.DataFrame, vix: pd.Series, days: pd.DatetimeIndex) -> list[dict[str, str]]:
    where = f"sessions {days[0].date()} to {days[-1].date()}"
    no_vx = [d for d in days if d not in cm.index]
    no_vix = [d for d in days if d not in vix.index]
    return [
        {"check": f"{where} without a same-day VX settlement (VXCM carried, the latest CM30 used)",
         "value": f"{len(no_vx)}: " + "; ".join(str(d.date()) for d in no_vx)},
        {"check": f"{where} without a same-day VIX close (the latest before used)",
         "value": f"{len(no_vix)}: " + "; ".join(str(d.date()) for d in no_vix)},
    ]  # fmt: skip


# ── stages ────────────────────────────────────────────────────────────


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, cm, vix = inputs()
    results = books(panel, volumes, cm, vix, holdout=s1t.HOLDOUT_ROWS)
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
                  VXCM: panel[VXCM].pct_change(fill_method=None).reindex(daily[S0_NAME].index)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip
    decisions(results[PRIMARY].events, cm, vix).to_csv(RESULTS / "walkforward_primary_decisions.csv", index=False)
    drift = drift_rows(results, panel)
    drift.to_csv(RESULTS / "walkforward_drift.csv", index=False)

    stress = pd.DataFrame([{"window": label, **{name: s1t.period_return(r, a, b) for name, r in daily.items()}}
                           for label, (a, b) in STRESS.items()])  # fmt: skip
    stress.to_csv(RESULTS / "walkforward_stress.csv", index=False)
    boot = s1t.block_bootstrap(s1t.excess_returns(daily[PRIMARY], panel))
    pd.DataFrame([{"book": PRIMARY, "percentile": p, "sharpe_excess": float(np.percentile(boot, p))}
                  for p in (5, 25, 50, 75, 95)]).to_csv(RESULTS / "walkforward_bootstrap.csv", index=False)  # fmt: skip

    s0_2008 = s1t.measure(daily[S0_NAME], panel)["sharpe_excess"]
    r2_s0 = pd.read_csv(HERE.parent / "r2_vol_positioning" / "results" / "walkforward_h2_books.csv").set_index(
        "book").loc["S0", "sharpe_excess"]  # fmt: skip
    days = panel.index[: len(panel) - s1t.HOLDOUT_ROWS]
    checks = [{"check": "panel", "value": f"{panel.index[0].date()} to {panel.index[-1].date()}, {len(panel)} sessions; "
               f"holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()}"},
              {"check": "VXCM on the panel", "value": f"from {panel[VXCM].first_valid_index().date()}, "
               f"{int(panel[VXCM].isna().sum())} sessions without a level"},
              *coverage(panel, cm, vix, days[days >= pd.Timestamp(FIRST_DECISION)]),
              {"check": "S0 from 2008-01-02: excess Sharpe here, and in R2's results",
               "value": f"{s0_2008:.6f} here, {r2_s0:.6f} in R2's, {'equal' if abs(s0_2008 - r2_s0) < 1e-9 else 'DIFFERENT'}"}]  # fmt: skip
    pd.DataFrame(checks).to_csv(RESULTS / "data_checks.csv", index=False)

    t = table.set_index("book")
    b, s0, mix = t.loc[PRIMARY], t.loc[S0_NAME], t.loc[MIX]
    s = stress.set_index("window")
    worst_variant = min(t.loc[v, "sharpe_excess"] for v in VARIANTS)
    p5 = float(np.percentile(boot, 5))
    worst_window = float(s[PRIMARY].min())
    mix_margin = float((s[MIX] - s[S0_NAME]).min())
    criteria = [("R3", PRIMARY, c, float(v), bool(p)) for c, v, p in [
        ("1. excess Sharpe >= 0.40", b["sharpe_excess"], b["sharpe_excess"] >= 0.40),
        ("2. deflated Sharpe >= 0.90 (n 27)", b["deflated_sharpe"], b["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile of the excess Sharpe > 0", p5, p5 > 0),
        ("4. every variant's excess Sharpe > 0 (worst variant)", worst_variant, worst_variant > 0),
        ("5. max drawdown no worse than -20%", b["max_drawdown"], b["max_drawdown"] >= -0.20),
        ("6. cost drag <= 1% a year", b["cost_drag"], b["cost_drag"] <= 0.01),
        ("7. no day outside the mandate", b["mandate_breach_days"], b["mandate_breach_days"] == 0),
        ("8. worst day no worse than -12%", b["worst_day"], b["worst_day"] >= -0.12),
        ("9. worst calendar month no worse than -15%", b["worst_month"], b["worst_month"] >= -0.15),
        ("10. each stress window no worse than -15% (worst window)", worst_window, worst_window >= -0.15),
        ("11a. 50/50 S0 + R3's excess Sharpe > S0's (difference)", mix["sharpe_excess"] - s0["sharpe_excess"],
         mix["sharpe_excess"] > s0["sharpe_excess"]),
        ("11b. 50/50 S0 + R3 loses no more than S0 in each stress window (smallest margin)", mix_margin,
         bool((s[MIX] >= s[S0_NAME]).all())),
    ]]  # fmt: skip
    pd.DataFrame(criteria, columns=["hypothesis", "book", "criterion", "value", "pass"]).to_csv(
        RESULTS / "walkforward_criteria.csv", index=False)  # fmt: skip
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, cm, vix = inputs()
    start = panel.index[-s1t.HOLDOUT_ROWS]
    results = books(panel, volumes, cm, vix, holdout=0, variants=False)
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
    pd.DataFrame({**daily, VXCM: panel[VXCM].pct_change(fill_method=None).reindex(daily[S0_NAME].index)}).to_csv(
        RESULTS / "holdout_daily_returns.csv")  # fmt: skip
    drift_rows(results, panel, since=start).to_csv(RESULTS / "holdout_drift.csv", index=False)
    pd.DataFrame(coverage(panel, cm, vix, panel.index[-s1t.HOLDOUT_ROWS :])).to_csv(
        RESULTS / "holdout_data_checks.csv", index=False)  # fmt: skip

    b = table.set_index("book").loc[PRIMARY]
    killed = bool(b["sharpe_excess"] < -0.5 or b["max_drawdown"] < -0.15 or b["worst_day"] < -0.12)
    holdout = pd.DataFrame([{"hypothesis": "R3", "book": PRIMARY,
                             "rule": "killed if its excess Sharpe is below -0.5, its drawdown worse than -15%, "
                             "or its worst day worse than -12%",
                             "sharpe_excess": b["sharpe_excess"], "max_drawdown": b["max_drawdown"],
                             "worst_day": b["worst_day"], "killed": killed}])  # fmt: skip
    holdout.to_csv(RESULTS / "holdout_criteria.csv", index=False)

    # the verdict, by HYPOTHESIS.md's rule: every criterion of the primary, and no holdout kill
    wf = pd.read_csv(RESULTS / "walkforward_criteria.csv")
    failed = wf.loc[~wf["pass"].astype(bool), "criterion"].tolist()
    pd.DataFrame([{"hypothesis": "R3", "criteria_passed": int(wf["pass"].astype(bool).sum()), "criteria": len(wf),
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
