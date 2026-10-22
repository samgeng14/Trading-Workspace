"""
r4_overnight.py — R4 through the research gate: SPY held from each close to the next open (the
primary), QQQ the same (V1) and SPY on weeknights only (V2), cash in the day, and the reference
books. HYPOTHESIS.md has the rules, costs, split and criteria, amended at review before any run.

    python r4_overnight.py                      # the walk-forward, then the holdout: every number
    python r4_overnight.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python r4_overnight.py --stage holdout      # the holdout

Use the repo's .venv Python. Each rule enters the C++ walk-forward as a constructed instrument
whose level steps once a night by the round trip's net value (HYPOTHESIS.md, "The instrument").
Reuses research/s1_trend's panel, harness and measures, so the numbers compare with S1's, S3a's,
R1's, R2's and R3's. Reads the prices and dividends through data.py (nothing is downloaded) and
writes results/.
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
from dv02_engine.costs import CostModel  # noqa: E402
from dv02_engine.walkforward import walk_forward  # noqa: E402

RESULTS = HERE / "results"
R1_TRIALS = HERE.parent / "r1_calendar_macro" / "results" / "trials.csv"
R1_BOOKS = HERE.parent / "r1_calendar_macro" / "results" / "walkforward_books.csv"
R2_BOOKS = HERE.parent / "r2_vol_positioning" / "results" / "walkforward_h2_books.csv"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
PANEL_END = "2026-10-16"  # R1's, R2's and R3's panel: the holdout is its last 756 sessions, from 2023-10-12
FIRST_DECISION = s1t.FIRST_DECISION  # 2008-01-02
N_TRIALS = 33  # S1's 8 + S3a's 6 + R2's 9 + R3's 4 + R1's 3 + these 3
TRIM_EVERY = 21  # the constant books are re-sized monthly, only to trim the drift
S0_DIVIDENDS_FROM = pd.Timestamp("2016-10-19")  # SPY's first total-return close in S1's panel

# HYPOTHESIS.md's costs per side (amended at review): fractions of the dollars traded
COMMISSION = 0.005  # dollars a share, IBKR Pro Fixed
SEC_FEE = 0.30e-4  # on sales
ALLOWANCE_CLOSE = 0.1e-4  # impact: the close is the closing auction's own price
ALLOWANCE_OPEN = 0.5e-4  # impact and the open's uncertainty
MULTIPLES = (0, 1, 2, 3)
BASES = ("SPY_ON", "QQQ_ON", "SPY_WN", "SPY_ID")


def column(base: str, k: int) -> str:
    """The panel column of a constructed series at k times the costs."""
    return base if k == 1 else f"{base}_{k}X"


# R1's CostModel (S1's, with SPY's and BIL's half-spreads at 1.0 bps). The constructed series
# carry their round trips' costs inside them, so the harness charges them no spread and has no
# volumes for them; SPY held (SPY_TR) pays SPY's.
NO_SPREAD = [column(b, k) for b in BASES for k in MULTIPLES] + ["SPY_ON_S0"]
COSTS = CostModel(
    half_spread_bps=2.0,
    half_spread_bps_by_symbol={"SPY": 1.0, "BIL": 1.0, "SPY_TR": 1.0, **{c: 0.0 for c in NO_SPREAD}},
    impact_k=0.5,
    borrow_bps=25.0,
    borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
    cash_interest_bps=0.0,  # as S1's: idle money earns nothing
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
PRIMARY = "R4 primary: SPY every night"
V1 = "R4 V1: QQQ every night"
V2 = "R4 V2: SPY on weeknights"
VARIANTS = (V1, V2)
SPY_HELD = "SPY held (with dividends)"
COMPLEMENT = "the intraday complement: SPY from each open to its close (reference)"
S0_NAME = "S0"
PRIMARY_S0 = "R4 primary, dividends as S0's (for the 50/50)"
MIX = "50/50 S0 + R4 primary (dividends as S0's)"
SERIES = {PRIMARY: "SPY_ON", V1: "QQQ_ON", V2: "SPY_WN", COMPLEMENT: "SPY_ID", PRIMARY_S0: "SPY_ON_S0",
          SPY_HELD: "SPY_TR"}  # fmt: skip


# ── the constructed series ────────────────────────────────────────────


def bars(symbol: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    """The master's open and close on the panel's sessions, and the cash going ex on each."""
    m = data.master(symbol).loc[index[0] : index[-1]]
    if not m.index.equals(index):
        raise ValueError(f"{symbol}'s sessions in the master aren't the panel's")
    d = data.dividends(symbol).loc[index[0] : index[-1]]
    if len(d.index.difference(index)):
        raise ValueError(f"{symbol} has ex-dates off the panel's sessions: {list(d.index.difference(index))}")
    return pd.DataFrame({"open": m["open"], "close": m["close"], "dividend": d.reindex(index).fillna(0.0)})


def nights(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Each session t stands for the night from the prior session's close to t's open. Every
    rule sits out the night after an early close (the 15:35 run is after the 13:00 close); V2
    also every night whose next session isn't the next calendar day."""
    prior = index[:-1]
    early = np.array([data.clock.is_early_close(d.date()) for d in prior], dtype=bool)
    next_day = np.asarray((index[1:] - prior).days == 1)
    out = pd.DataFrame(
        {"after_early_close": np.concatenate(([False], early)), "next_calendar_day": np.concatenate(([False], next_day))},
        index=index,
    )
    out["held_every_night"] = ~out["after_early_close"]
    out.iloc[0, out.columns.get_loc("held_every_night")] = False  # no night before the panel's first close
    out["held_weeknight"] = out["held_every_night"] & out["next_calendar_day"]
    return out


def overnight(b: pd.DataFrame, held: pd.Series, k: int, dividend: pd.Series | None = None):
    """HYPOTHESIS.md's L_t at k times the costs: bought in the prior closing auction, sold in
    this session's opening auction, owed the cash going ex today; flat on a night sat out.
    Also each session's cost per dollar held at the prior close."""
    d = b["dividend"] if dividend is None else dividend
    prior = b["close"].shift(1)
    c_buy = k * (COMMISSION / prior + ALLOWANCE_CLOSE)
    c_sell = k * (COMMISSION / b["open"] + SEC_FEE + ALLOWANCE_OPEN)
    step = ((1 - c_buy) * (b["open"] * (1 - c_sell) + d) / prior).where(held, 1.0)
    cost = (c_buy + (1 - c_buy) * c_sell * b["open"] / prior).where(held, 0.0)
    return b["close"].iloc[0] * step.cumprod(), cost


def intraday(b: pd.DataFrame, k: int):
    """SPY bought in each opening auction and sold in the same day's closing auction, at k times
    the costs (each auction's allowance: 0.5 bps at the open, 0.1 at the close; the SEC fee on
    the sale). No dividend: the share goes ex before the open. Also the cost per dollar."""
    c_buy = k * (COMMISSION / b["open"] + ALLOWANCE_OPEN)
    c_sell = k * (COMMISSION / b["close"] + SEC_FEE + ALLOWANCE_CLOSE)
    later = pd.Series(np.arange(len(b)) > 0, index=b.index)  # the level starts at the first close
    step = ((1 - c_buy) * b["close"] * (1 - c_sell) / b["open"]).where(later, 1.0)
    cost = (c_buy + (1 - c_buy) * c_sell * b["close"] / b["open"]).where(later, 0.0)
    return b["close"].iloc[0] * step.cumprod(), cost


def total_return(b: pd.DataFrame) -> pd.Series:
    """SPY close to close with its dividends: SPY held."""
    step = ((b["close"] + b["dividend"]) / b["close"].shift(1)).fillna(1.0)
    return b["close"].iloc[0] * step.cumprod()


def constructed(index: pd.DatetimeIndex):
    """(levels, cost per dollar, nights, bars): every constructed series on the panel's sessions."""
    spy, qqq = bars("SPY", index), bars("QQQ", index)
    n = nights(index)
    levels, cost = {}, {}
    for k in MULTIPLES:
        for base, (b, held) in {"SPY_ON": (spy, n["held_every_night"]), "QQQ_ON": (qqq, n["held_every_night"]),
                                "SPY_WN": (spy, n["held_weeknight"])}.items():  # fmt: skip
            levels[column(base, k)], cost[column(base, k)] = overnight(b, held, k)
        levels[column("SPY_ID", k)], cost[column("SPY_ID", k)] = intraday(spy, k)
    before = spy["dividend"].where(index >= S0_DIVIDENDS_FROM, 0.0)
    levels["SPY_ON_S0"], cost["SPY_ON_S0"] = overnight(spy, n["held_every_night"], 1, dividend=before)
    levels["SPY_TR"] = total_return(spy)
    cost["SPY_TR"] = pd.Series(0.0, index=index)
    return pd.DataFrame(levels), pd.DataFrame(cost), n, {"SPY": spy, "QQQ": qqq}


# ── running and measuring ─────────────────────────────────────────────


def inputs():
    """S1's panel with the constructed series added, S1's volumes (none for those), and the
    series' costs, nights and bars."""
    panel, volumes, _ = s1t.load_panel()
    panel, volumes = panel.loc[:PANEL_END], volumes.loc[:PANEL_END]
    levels, cost, n, b = constructed(panel.index)
    return pd.concat([panel, levels], axis=1), volumes, cost, n, b


def constant(col: str):
    """col at 99.5% at every rebalance."""

    def strategy(history: pd.DataFrame) -> dict[str, float]:
        return {col: BUDGET}

    return strategy


def held_once(col: str):
    """col bought at 99.5% at the first trade and never rebalanced (R1's and R2's SPY held)."""
    state = {"bought": False}

    def strategy(history: pd.DataFrame):
        if state["bought"]:
            return None
        state["bought"] = True
        return {col: BUDGET}

    return strategy


def run(panel, volumes, strategy, *, rebalance_every: int, holdout: int):
    """S1's harness (lag 1, the mandate check, yearly test blocks) with this study's costs."""
    first = int(panel.index.searchsorted(pd.Timestamp(FIRST_DECISION)))
    return walk_forward(panel, strategy, train=first, test=s1t.TEST_ROWS, costs=COSTS, n_trials=N_TRIALS,
                        holdout=holdout, rebalance_every=rebalance_every, lag=1, volumes=volumes,
                        mandate=True)  # fmt: skip


def books(panel, volumes, *, holdout: int, variants: bool = True) -> dict[str, object]:
    plan = {PRIMARY: (constant("SPY_ON"), TRIM_EVERY), COMPLEMENT: (constant("SPY_ID"), TRIM_EVERY),
            SPY_HELD: (held_once("SPY_TR"), 1), S0_NAME: (s1t.s0(s1t.s0_targets()), 1),
            PRIMARY_S0: (constant("SPY_ON_S0"), TRIM_EVERY)}  # fmt: skip
    if variants:
        plan.update({V1: (constant("QQQ_ON"), TRIM_EVERY), V2: (constant("SPY_WN"), TRIM_EVERY)})
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


def dated(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.DatetimeIndex(pd.to_datetime(out["date"]), name="date")
    return out


def window_costs(wf, col: str | None, cost: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, float]:
    """Over the days: the series' own costs (each night's round trip on the dollars held at the
    prior close) and the harness's, in dollars and a year over the average value."""
    res = dated(wf.backtest["backtest_results"]).loc[days]
    years = len(days) / TRADING_DAYS
    value = float(res["portfolio_value"].mean())
    harness = float(res["daily_total_cost"].sum())
    series = 0.0
    if col is not None:
        held = dated(wf.backtest["backtest_positions"])[col].shift(1).fillna(0.0)
        series = float((held.loc[days] * cost[col].reindex(days)).sum())
    return {"series_cost": series, "harness_cost": harness, "series_cost_drag": series / years / value,
            "harness_cost_drag": harness / years / value, "cost_drag": (series + harness) / years / value}  # fmt: skip


def held_count(name: str, n: pd.DataFrame, days: pd.DatetimeIndex) -> float:
    flag = {PRIMARY: "held_every_night", V1: "held_every_night", PRIMARY_S0: "held_every_night",
            V2: "held_weeknight"}.get(name)  # fmt: skip
    return float(n.loc[days, flag].sum()) if flag else math.nan


def book_rows(results, daily, panel, cost, n, *, trial_sharpes=None) -> pd.DataFrame:
    rows = []
    for name, r in daily.items():
        row = {"book": name, **s1t.measure(r, panel)}
        wm, wm_at = worst_month(r)
        row.update(worst_day=float(r.min()), worst_day_date=r.idxmin().date().isoformat(), worst_month=wm,
                   worst_month_at=wm_at, nights_held=held_count(name, n, r.index))  # fmt: skip
        if trial_sharpes is not None:
            dsr, threshold = s1t.deflated(s1t.excess_returns(r, panel), N_TRIALS, trial_sharpes)
            row.update(deflated_sharpe=dsr, deflated_sharpe_threshold=threshold)
        if name in results:
            wf = results[name]
            row.update(window_costs(wf, SERIES.get(name), cost, r.index))
            if trial_sharpes is not None:
                c = wf.combined
                metrics = wf.backtest["backtest_metrics"].set_index("metric")["value"]
                assert abs(c["cost_drag"] - row["harness_cost_drag"]) < 1e-12, name
                row.update(harness_turnover=c["turnover"], spread_cost=c["spread_cost"],
                           commission_cost=c["commission_cost"], impact_cost=c["impact_cost"],
                           borrow_cost=c["borrow_cost"],
                           unmeasured_impact_trades=float(metrics.get("unmeasured_impact_trades", math.nan)),
                           mandate_breach_days=c["mandate_breach_days"], max_gross_exposure=c["max_gross_exposure"],
                           decisions=len(wf.events))  # fmt: skip
        row["correlation_with_s0"] = float(r.corr(daily[S0_NAME]))
        row["correlation_with_spy_held"] = float(r.corr(daily[SPY_HELD]))
        rows.append(row)
    table = pd.DataFrame(rows)
    # the 50/50 book: half of each half's costs and turnover, both halves' mandate records (as S3a's, R2's, R3's)
    t = table.set_index("book")
    for k in ("series_cost_drag", "harness_cost_drag", "cost_drag", "harness_turnover"):
        if k in t.columns:
            table.loc[table["book"] == MIX, k] = 0.5 * t.loc[S0_NAME, k] + 0.5 * t.loc[PRIMARY_S0, k]
    if "mandate_breach_days" in t.columns:
        table.loc[table["book"] == MIX, "mandate_breach_days"] = (
            t.loc[S0_NAME, "mandate_breach_days"] + t.loc[PRIMARY_S0, "mandate_breach_days"])  # fmt: skip
    return table


def trials(daily, panel) -> pd.DataFrame:
    """The 33 trials: R1's file lists the first 30; these 3 from this run."""
    earlier = pd.read_csv(R1_TRIALS)
    ours = pd.DataFrame([{"study": "R4", "trial": n, "sharpe_excess": s1t.measure(daily[n], panel)["sharpe_excess"]}
                         for n in (PRIMARY, *VARIANTS)])  # fmt: skip
    out = pd.concat([earlier, ours], ignore_index=True)
    assert len(out) == N_TRIALS, len(out)
    return out


def sensitivity(panel, volumes, cost, n, main: dict[str, object], days: pd.DatetimeIndex) -> pd.DataFrame:
    """The primary, the variants and the intraday complement rebuilt at 0, 1, 2 and 3 times the
    costs. The harness's own costs (the monthly trims' commission) stay as they are."""
    rows = []
    for name, base in ((PRIMARY, "SPY_ON"), (V1, "QQQ_ON"), (V2, "SPY_WN"), (COMPLEMENT, "SPY_ID")):
        for k in MULTIPLES:
            col = column(base, k)
            wf = main[name] if k == 1 else run(panel, volumes, constant(col), rebalance_every=TRIM_EVERY,
                                                holdout=s1t.HOLDOUT_ROWS)  # fmt: skip
            r = wf.equity["daily_return"].loc[days]
            m = s1t.measure(r, panel)
            traded = cost.loc[days, col][cost.loc[days, column(base, 1)] > 0]
            rows.append({"book": name, "series": col, "cost_multiple": k, "sharpe_excess": m["sharpe_excess"],
                         "sharpe_excess_se": m["sharpe_excess_se"], "annual_return": m["annual_return"],
                         "annual_excess_return": m["annual_excess_return"], "max_drawdown": m["max_drawdown"],
                         "median_round_trip_bps": float(traded.median() * 1e4),
                         **window_costs(wf, col, cost, days)})  # fmt: skip
    return pd.DataFrame(rows)


def night_checks(n: pd.DataFrame, b: dict[str, pd.DataFrame], days: pd.DatetimeIndex, label: str) -> list[dict]:
    """What each rule holds over the days, the nights sat out and the dividends inside."""
    w = n.loc[days]
    sat_out = [d.date().isoformat() for d in w.index[w["after_early_close"]]]
    out = [{"check": f"{label}: sessions {days[0].date()} to {days[-1].date()}", "value": str(len(days))},
           {"check": f"{label}: nights held by the primary and V1 (every night but those after an early close)",
            "value": str(int(w["held_every_night"].sum()))},
           {"check": f"{label}: nights held by V2 (weeknights: the next session is the next calendar day)",
            "value": str(int(w["held_weeknight"].sum()))},
           {"check": f"{label}: nights after an early close, sat out by every rule",
            "value": f"{len(sat_out)}: " + "; ".join(sat_out)}]  # fmt: skip
    for symbol, frame in b.items():
        d = frame.loc[days, "dividend"]
        out.append({"check": f"{label}: {symbol} ex-dates inside, and their cash a share",
                    "value": f"{int((d > 0).sum())}, ${d.sum():.4f}"})  # fmt: skip
        held_ex = int(((d > 0) & w["held_every_night"]).sum())
        out.append({"check": f"{label}: {symbol} ex-dates on a night the primary or V1 holds",
                    "value": str(held_ex)})  # fmt: skip
    return out


def series_checks(panel, levels_cols, b: dict[str, pd.DataFrame], days: pd.DatetimeIndex) -> list[dict]:
    """The constructed series against SPY: the gross night times the gross day is SPY's total
    return, to rounding on a session without a dividend; on an ex-date they differ by the
    dividend's timing (owed at the open, against reinvested at the close)."""
    spy = b["SPY"].loc[: days[-1]]
    night = (spy["open"] + spy["dividend"]) / spy["close"].shift(1)
    day = spy["close"] / spy["open"]
    total = (spy["close"] + spy["dividend"]) / spy["close"].shift(1)
    gap = (night * day / total - 1).loc[days].abs()
    ex = spy.loc[days, "dividend"] > 0
    return [{"check": "the constructed levels start at each ETF's close on the panel's first date",
             "value": "; ".join(f"{c} {panel[c].iloc[0]:.2f}" for c in levels_cols)},
            {"check": "SPY's gross night x gross day against its total return, largest gap on a session "
                      "without a dividend, and on an ex-date",
             "value": f"{gap[~ex].max():.2e}; {gap[ex].max():.2e} on {gap[ex].idxmax().date()}"}]  # fmt: skip


def series_frame(panel, n, b, days) -> pd.DataFrame:
    """The constructed series' levels on the days, with each night's flags and the dividends."""
    levels = panel.loc[days, [*NO_SPREAD, "SPY_TR"]]
    return pd.concat([levels, n.loc[days], b["SPY"].loc[days, ["open", "close", "dividend"]].add_prefix("SPY_"),
                      b["QQQ"].loc[days, ["open", "close", "dividend"]].add_prefix("QQQ_")], axis=1)  # fmt: skip


# ── stages ────────────────────────────────────────────────────────────


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, cost, n, b = inputs()
    results = books(panel, volumes, holdout=s1t.HOLDOUT_ROWS)
    daily = on_common_days(results)
    daily[MIX] = 0.5 * daily[S0_NAME] + 0.5 * daily[PRIMARY_S0]
    days = daily[S0_NAME].index

    trial_table = trials(daily, panel)
    trial_table.to_csv(RESULTS / "trials.csv", index=False)
    trial_sharpes = trial_table["sharpe_excess"].tolist()
    variance = float(np.var(trial_sharpes, ddof=1))
    pd.DataFrame([{"n_trials": N_TRIALS, "sharpe_variance_annualized": variance,
                   "sharpe_variance_per_day": variance / TRADING_DAYS}]).to_csv(
        RESULTS / "deflated_sharpe_inputs.csv", index=False)  # fmt: skip

    table = book_rows(results, daily, panel, cost, n, trial_sharpes=trial_sharpes)
    table.to_csv(RESULTS / "walkforward_books.csv", index=False)
    pd.DataFrame({**daily, "BIL": panel[CASH].pct_change(fill_method=None).reindex(days)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip
    pd.DataFrame(daily).corr().rename_axis("book").to_csv(RESULTS / "walkforward_correlations.csv")
    before_holdout = panel.index[: len(panel) - s1t.HOLDOUT_ROWS]
    series_frame(panel, n, b, before_holdout).to_csv(RESULTS / "walkforward_constructed_series.csv")

    stress = pd.DataFrame([{"window": label, **{name: s1t.period_return(r, a, e) for name, r in daily.items()}}
                           for label, (a, e) in STRESS.items()])  # fmt: skip
    stress.to_csv(RESULTS / "walkforward_stress.csv", index=False)
    boot = s1t.block_bootstrap(s1t.excess_returns(daily[PRIMARY], panel))
    pd.DataFrame([{"book": PRIMARY, "percentile": p, "sharpe_excess": float(np.percentile(boot, p))}
                  for p in (5, 25, 50, 75, 95)]).to_csv(RESULTS / "walkforward_bootstrap.csv", index=False)  # fmt: skip
    sens = sensitivity(panel, volumes, cost, n, results, days)
    sens.to_csv(RESULTS / "walkforward_cost_sensitivity.csv", index=False)

    # the data: S0 with S1's own costs must score R2's 0.268481, and with R1's costs R1's figure
    s0_s1 = s1t.run(panel, volumes, s1t.s0(s1t.s0_targets()), rebalance_every=1, holdout=s1t.HOLDOUT_ROWS,
                    n_trials=1).equity["daily_return"].loc[days]  # fmt: skip
    s0_s1_sharpe = s1t.measure(s0_s1, panel)["sharpe_excess"]
    r2_s0 = pd.read_csv(R2_BOOKS).set_index("book").loc["S0", "sharpe_excess"]
    r1_s0 = pd.read_csv(R1_BOOKS).set_index("book").loc["S0", "sharpe_excess"]
    s0_here = s1t.measure(daily[S0_NAME], panel)["sharpe_excess"]
    same = lambda x, y: "equal" if abs(x - y) < 1e-9 else "DIFFERENT"  # noqa: E731
    checks = [{"check": "panel", "value": f"{panel.index[0].date()} to {panel.index[-1].date()}, {len(panel)} sessions; "
               f"holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()}"},
              {"check": "the master's SPY and QQQ sessions against the panel's", "value": "the same"},
              {"check": "S0 from 2008-01-02 with S1's costs: excess Sharpe here, and in R2's results",
               "value": f"{s0_s1_sharpe:.6f} here, {r2_s0:.6f} in R2's, {same(s0_s1_sharpe, r2_s0)}"},
              {"check": "S0 from 2008-01-02 with R1's costs (this study's for S0): here, and in R1's results",
               "value": f"{s0_here:.6f} here, {r1_s0:.6f} in R1's, {same(s0_here, r1_s0)}"},
              {"check": "SPY held", "value": "SPY_TR bought at 99.5% at the first trade, never rebalanced (R1's, R2's)"},
              {"check": "the intraday complement's allowance", "value": "each auction's: 0.5 bps on the buy at the "
               "open, 0.1 bps on the sale at the close, with the SEC fee"},
              *night_checks(n, b, days, "walk-forward"),
              *series_checks(panel, ["SPY_ON", "QQQ_ON", "SPY_WN", "SPY_ID", "SPY_TR"], b, days)]  # fmt: skip
    pd.DataFrame(checks).to_csv(RESULTS / "data_checks.csv", index=False)

    t = table.set_index("book")
    p, spy, s0, mix = t.loc[PRIMARY], t.loc[SPY_HELD], t.loc[S0_NAME], t.loc[MIX]
    s = stress.set_index("window")
    worst_variant = min(t.loc[v, "sharpe_excess"] for v in VARIANTS)
    p5 = float(np.percentile(boot, 5))
    doubled = float(sens.set_index("series").loc[column("SPY_ON", 2), "sharpe_excess"])
    margin = float((s[PRIMARY] - s[SPY_HELD]).min())
    mix_margin = float((s[MIX] - s[S0_NAME]).min())
    criteria = [("R4", PRIMARY, c, float(v), float(lim), bool(ok)) for c, v, lim, ok in [
        ("1. excess Sharpe >= 0.40", p["sharpe_excess"], 0.40, p["sharpe_excess"] >= 0.40),
        ("1b. excess Sharpe above SPY held's (SPY_TR), the limit", p["sharpe_excess"], spy["sharpe_excess"],
         p["sharpe_excess"] > spy["sharpe_excess"]),
        ("2. deflated Sharpe >= 0.90 (n 33)", p["deflated_sharpe"], 0.90, p["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile of the excess Sharpe > 0", p5, 0.0, p5 > 0),
        ("4. every variant's excess Sharpe > 0 (worst variant)", worst_variant, 0.0, worst_variant > 0),
        ("5. max drawdown no deeper than 60% of SPY held's, the limit", p["max_drawdown"], 0.6 * spy["max_drawdown"],
         p["max_drawdown"] >= 0.6 * spy["max_drawdown"]),
        ("6. no day outside the mandate", p["mandate_breach_days"], 0.0, p["mandate_breach_days"] == 0),
        ("7. excess Sharpe >= 0.20 with every cost doubled", doubled, 0.20, doubled >= 0.20),
        ("8. worst night no worse than -12%", p["worst_day"], -0.12, p["worst_day"] >= -0.12),
        ("9. worst calendar month no worse than SPY held's, the limit", p["worst_month"], spy["worst_month"],
         p["worst_month"] >= spy["worst_month"]),
        ("10. a return above SPY held's in each stress window (smallest margin)", margin, 0.0,
         bool((s[PRIMARY] > s[SPY_HELD]).all())),
        ("11a. 50/50 S0 + R4's excess Sharpe > S0's (difference)", mix["sharpe_excess"] - s0["sharpe_excess"], 0.0,
         mix["sharpe_excess"] > s0["sharpe_excess"]),
        ("11b. 50/50 S0 + R4 loses no more than S0 in each stress window (smallest margin)", mix_margin, 0.0,
         bool((s[MIX] >= s[S0_NAME]).all())),
    ]]  # fmt: skip
    pd.DataFrame(criteria, columns=["hypothesis", "book", "criterion", "value", "limit", "pass"]).to_csv(
        RESULTS / "walkforward_criteria.csv", index=False)  # fmt: skip
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, cost, n, b = inputs()
    start = panel.index[-s1t.HOLDOUT_ROWS]
    results = books(panel, volumes, holdout=0, variants=False)
    daily = {k: v.loc[start:] for k, v in on_common_days(results).items()}
    daily[MIX] = 0.5 * daily[S0_NAME] + 0.5 * daily[PRIMARY_S0]
    days = daily[S0_NAME].index

    table = book_rows(results, daily, panel, cost, n)
    table.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame({**daily, "BIL": panel[CASH].pct_change(fill_method=None).reindex(days)}).to_csv(
        RESULTS / "holdout_daily_returns.csv")  # fmt: skip
    series_frame(panel, n, b, days).to_csv(RESULTS / "holdout_constructed_series.csv")
    pd.DataFrame(night_checks(n, b, days, "holdout")).to_csv(RESULTS / "holdout_data_checks.csv", index=False)

    t = table.set_index("book")
    p, spy = t.loc[PRIMARY], t.loc[SPY_HELD]
    killed = bool(p["sharpe_excess"] < -0.5 or p["max_drawdown"] < spy["max_drawdown"] or p["worst_day"] < -0.12)
    pd.DataFrame([{"hypothesis": "R4", "book": PRIMARY,
                   "rule": "killed if its excess Sharpe is below -0.5, its drawdown deeper than SPY held's over the "
                           "holdout, or a night worse than -12%",
                   "sharpe_excess": p["sharpe_excess"], "max_drawdown": p["max_drawdown"],
                   "spy_held_max_drawdown": spy["max_drawdown"], "worst_night": p["worst_day"],
                   "killed": killed}]).to_csv(RESULTS / "holdout_criteria.csv", index=False)  # fmt: skip

    # the verdict, by HYPOTHESIS.md's rule: every criterion of the primary, and no holdout kill
    wf = pd.read_csv(RESULTS / "walkforward_criteria.csv")
    failed = wf.loc[~wf["pass"].astype(bool), "criterion"].tolist()
    pd.DataFrame([{"hypothesis": "R4", "criteria_passed": int(wf["pass"].astype(bool).sum()), "criteria": len(wf),
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
