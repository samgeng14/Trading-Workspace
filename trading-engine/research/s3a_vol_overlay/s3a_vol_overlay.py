"""
s3a_vol_overlay.py — H1 (S3a: S0 scaled down when its volatility is high) and H2 (S0 with a
trend overlay) through the research gate, against S0. HYPOTHESIS.md has the rules, split and
criteria; REPORT.md the verdict.

    python s3a_vol_overlay.py                      # the walk-forward, then the holdout: every number
    python s3a_vol_overlay.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python s3a_vol_overlay.py --stage holdout      # the holdout

Use the repo's .venv Python. Reuses research/s1_trend/s1_trend.py's data, costs, backtest
harness and measures, so the numbers compare with S1's; writes results/.
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
import s1_trend as s1t  # noqa: E402

RESULTS = HERE / "results"
CASH, BUDGET, TRADING_DAYS = s1t.CASH, s1t.BUDGET, s1t.TRADING_DAYS
BAND = 0.10  # H1 trades between S0's rebalances when its scale moves this much
H1_VARIANTS = [(21, 0.10), (63, 0.10), (21, 0.08), (63, 0.08)]  # (window, target vol)
H1_PRIMARY = (21, 0.10)
LOOKBACKS = (63, 126, 252)  # H2's trend filter, as S1's blend
S1_TRIALS = HERE.parent / "s1_trend" / "results" / "walkforward_variants.csv"
N_TRIALS = 14  # S1's 8 + H1's 4 + H2's 2
BLOCK, RESAMPLES, SEED = 63, 2_000, 21
SHARPE_SLACK = 0.05  # criteria 2 and 4: at least S0's excess Sharpe minus this


# ── strategies ────────────────────────────────────────────────────────


def s0_books(targets: pd.DataFrame) -> dict[pd.Timestamp, pd.Series]:
    """S0's target on each of its rebalance dates, scaled to the 99.5% budget."""
    return {d: g.set_index("symbol")["target_weight"].astype(float) * BUDGET for d, g in targets.groupby("rebalance_date")}


def scaled(base: pd.Series, scale: float) -> dict[str, float]:
    """The base book's risky weights times scale; BIL (S0's own BIL included) takes the rest."""
    book = {s: float(w) * scale for s, w in base.items() if s != CASH and w != 0}
    book[CASH] = BUDGET - sum(abs(w) for w in book.values())
    return book


def book_vol(history: pd.DataFrame, base: pd.Series, window: int) -> float:
    """Annualized volatility over the last `window` days of the base book's risky weights,
    applied to each asset's daily returns: what the book holds now, at its recent risk."""
    risky = base[(base.index != CASH) & (base != 0)]
    if risky.empty:
        return 0.0
    returns = history[risky.index].iloc[-(window + 1) :].pct_change(fill_method=None).iloc[1:]
    daily = returns.fillna(0.0).to_numpy() @ risky.to_numpy()
    return float(np.std(daily, ddof=1) * math.sqrt(TRADING_DAYS)) if len(daily) > 1 else 0.0


def h1(targets: pd.DataFrame, window: int, target: float):
    """H1: S0's latest target, every risky weight scaled by min(1, target / its recent vol);
    traded on S0's dates and when the scale moves by BAND or more."""
    by_date = s0_books(targets)
    state: dict[str, object] = {"base": None, "scale": None}

    def strategy(history: pd.DataFrame):
        fresh = by_date.get(history.index[-1])
        if fresh is not None:
            state["base"] = fresh
        base = state["base"]
        if base is None:
            return None
        vol = book_vol(history, base, window)
        scale = min(1.0, target / vol) if vol > 0 else 1.0
        if fresh is None and state["scale"] is not None and abs(scale - state["scale"]) < BAND:
            return None
        state["scale"] = scale
        return scaled(base, scale)

    return strategy


def h2_filter(targets: pd.DataFrame):
    """H2's variant: on S0's dates, a name in a downtrend (the mean sign of its available
    3, 6 and 12-month returns below 0) gives its weight to BIL."""
    by_date = s0_books(targets)

    def strategy(history: pd.DataFrame):
        base = by_date.get(history.index[-1])
        if base is None:
            return None
        kept = {}
        for symbol, w in base.items():
            if symbol == CASH or w == 0:
                continue
            closes = history[symbol].dropna()
            if len(closes) >= 64:
                signs = [np.sign(closes.iloc[-1] / closes.iloc[-1 - lb] - 1.0) for lb in LOOKBACKS if len(closes) > lb]
                if float(np.mean(signs)) < 0:
                    continue
            kept[symbol] = float(w)
        kept[CASH] = BUDGET - sum(abs(v) for v in kept.values())
        return kept

    return strategy


# ── measuring ─────────────────────────────────────────────────────────


def _sharpe(x: np.ndarray) -> float:
    sd = x.std(ddof=1)
    return float(x.mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else math.nan


def _max_drawdown(r: np.ndarray) -> float:
    growth = np.cumprod(1.0 + r)
    return float((growth / np.maximum.accumulate(growth) - 1.0).min())


def paired_bootstrap(book: pd.Series, s0: pd.Series, panel: pd.DataFrame) -> dict[str, float]:
    """The same 63-day blocks drawn for the book and S0: the spread of the differences in
    excess Sharpe and in max drawdown, and the probabilities criteria 3 and 4 read."""
    x_b = s1t.excess_returns(book, panel).to_numpy()
    x_0 = s1t.excess_returns(s0, panel).to_numpy()
    r_b, r_0 = book.to_numpy(), s0.to_numpy()
    n = len(r_b)
    rng = np.random.default_rng(SEED)
    blocks = math.ceil(n / BLOCK)
    d_sharpe, d_dd = np.empty(RESAMPLES), np.empty(RESAMPLES)
    for i in range(RESAMPLES):
        starts = rng.integers(0, n - BLOCK + 1, size=blocks)
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts])[:n]
        d_sharpe[i] = _sharpe(x_b[idx]) - _sharpe(x_0[idx])
        d_dd[i] = _max_drawdown(r_b[idx]) - _max_drawdown(r_0[idx])  # > 0: the book's is shallower
    return {
        "p_drawdown_shallower": float((d_dd > 0).mean()),
        "p_sharpe_within_slack": float((d_sharpe >= -SHARPE_SLACK).mean()),
        **{f"sharpe_diff_p{p}": float(np.percentile(d_sharpe, p)) for p in (5, 50, 95)},
        **{f"drawdown_diff_p{p}": float(np.percentile(d_dd, p)) for p in (5, 50, 95)},
    }


def risky_exposure(events: pd.DataFrame) -> dict[str, float]:
    """How invested the book was at its decisions: gross outside BIL."""
    risky = [c for c in events.columns if c not in ("decided", "executed", "fold", CASH)]
    gross = events[risky].abs().sum(axis=1)
    return {"avg_risky_gross": float(gross.mean()), "min_risky_gross": float(gross.min()), "decisions": float(len(events))}


def runs(panel, volumes, *, holdout: int) -> tuple[dict[str, pd.Series], dict[str, dict], dict[str, pd.DataFrame]]:
    """Daily returns, the harness's combined record and decisions, per book."""
    targets = s1t.s0_targets()
    books = {"S0": (s1t.s0(targets), 1)}
    for window, target in H1_VARIANTS:
        books[f"H1 W{window} target {target:.0%}"] = (h1(targets, window, target), 1)
    books["H2 S0 trend filter"] = (h2_filter(targets), 1)
    books["S1 blend long/flat"] = (s1t.s1("blend", "long_flat"), s1t.REBALANCE_EVERY)
    daily, combined, events = {}, {}, {}
    for name, (strategy, every) in books.items():
        wf = s1t.run(panel, volumes, strategy, rebalance_every=every, holdout=holdout, n_trials=N_TRIALS)
        daily[name], combined[name], events[name] = wf.equity["daily_return"], wf.combined, wf.events
    common = None
    for r in daily.values():
        common = r.index if common is None else common.intersection(r.index)
    daily = {k: v.loc[common] for k, v in daily.items()}
    # H2's primary: half the capital in each sleeve, daily returns mixed (S1's study's method)
    daily["H2 50/50 S0 + S1 long/flat"] = 0.5 * daily["S0"] + 0.5 * daily["S1 blend long/flat"]
    c0, c1 = combined["S0"], combined["S1 blend long/flat"]
    combined["H2 50/50 S0 + S1 long/flat"] = {
        "turnover": 0.5 * c0["turnover"] + 0.5 * c1["turnover"],
        "cost_drag": 0.5 * c0["cost_drag"] + 0.5 * c1["cost_drag"],
        "mandate_breach_days": c0["mandate_breach_days"] + c1["mandate_breach_days"],
    }
    return daily, combined, events


H1_NAME = f"H1 W{H1_PRIMARY[0]} target {H1_PRIMARY[1]:.0%}"
H2_NAME = "H2 50/50 S0 + S1 long/flat"
TRIAL_BOOKS = [f"H1 W{w} target {t:.0%}" for w, t in H1_VARIANTS] + [H2_NAME, "H2 S0 trend filter"]


# ── stages ────────────────────────────────────────────────────────────


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, _ = s1t.load_panel()
    daily, combined, events = runs(panel, volumes, holdout=s1t.HOLDOUT_ROWS)
    s0 = daily["S0"]

    # the deflated Sharpe over all 14 trials (reported; not a criterion: HYPOTHESIS.md)
    trial_sharpes = pd.read_csv(S1_TRIALS)["sharpe_excess"].tolist()
    trial_sharpes += [s1t.measure(daily[b], panel)["sharpe_excess"] for b in TRIAL_BOOKS]
    assert len(trial_sharpes) == N_TRIALS

    rows, boots = [], []
    for name, r in daily.items():
        row = {"book": name, **s1t.measure(r, panel)}
        dsr, threshold = s1t.deflated(s1t.excess_returns(r, panel), N_TRIALS, trial_sharpes)
        c = combined[name]
        row.update(turnover=c["turnover"], cost_drag=c["cost_drag"], mandate_breach_days=c["mandate_breach_days"],
                   deflated_sharpe=dsr, deflated_sharpe_threshold=threshold)  # fmt: skip
        if name in events:
            row.update(risky_exposure(events[name]))
        rows.append(row)
        if name != "S0":
            boots.append({"book": name, **paired_bootstrap(r, s0, panel)})
    books = pd.DataFrame(rows)
    books.to_csv(RESULTS / "walkforward_books.csv", index=False)
    boots = pd.DataFrame(boots)
    boots.to_csv(RESULTS / "walkforward_bootstrap.csv", index=False)
    pd.DataFrame({**daily, "BIL": panel[CASH].pct_change(fill_method=None).reindex(s0.index)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip
    events[H1_NAME].to_csv(RESULTS / "walkforward_h1_primary_decisions.csv", index=False)

    regimes = pd.DataFrame([
        {"period": label, **{name: s1t.period_return(r, a, b) for name, r in daily.items()}}
        for label, (a, b) in s1t.REGIMES.items()
    ])  # fmt: skip
    regimes.to_csv(RESULTS / "walkforward_regimes.csv", index=False)

    by_book, by_boot = books.set_index("book"), boots.set_index("book")
    s0_row = by_book.loc["S0"]
    criteria = []
    for hypothesis, name in (("H1", H1_NAME), ("H2", H2_NAME)):
        b, bb = by_book.loc[name], by_boot.loc[name]
        worse_than_s0 = {
            label: (s1t.period_return(daily[name], a, b_end), s1t.period_return(s0, a, b_end))
            for label, (a, b_end) in s1t.REGIMES.items() if label != "2020"
        }  # fmt: skip
        crisis_ok = all(book_r > s0_r for book_r, s0_r in worse_than_s0.values())
        checks = [
            ("1. max drawdown at most 60% of S0's", b["max_drawdown"] / s0_row["max_drawdown"],
             abs(b["max_drawdown"]) <= 0.60 * abs(s0_row["max_drawdown"])),
            ("2. excess Sharpe >= S0's - 0.05", b["sharpe_excess"] - s0_row["sharpe_excess"],
             b["sharpe_excess"] >= s0_row["sharpe_excess"] - SHARPE_SLACK),
            ("3. bootstrap P(drawdown shallower than S0's) >= 80%", bb["p_drawdown_shallower"],
             bb["p_drawdown_shallower"] >= 0.80),
            ("4. bootstrap P(excess Sharpe >= S0's - 0.05) >= 80%", bb["p_sharpe_within_slack"],
             bb["p_sharpe_within_slack"] >= 0.80),
            ("5. cost drag <= 0.5% a year", b["cost_drag"], b["cost_drag"] <= 0.005),
            ("6. loses less than S0 in 2008, the 2020 crash and 2022",
             min(book_r - s0_r for book_r, s0_r in worse_than_s0.values()), crisis_ok),
            ("7. no day outside the mandate", b["mandate_breach_days"], b["mandate_breach_days"] == 0),
        ]  # fmt: skip
        criteria += [{"hypothesis": hypothesis, "book": name, "criterion": c, "value": float(v), "pass": bool(p)}
                     for c, v, p in checks]  # fmt: skip
    pd.DataFrame(criteria).to_csv(RESULTS / "walkforward_criteria.csv", index=False)
    print(f"walk-forward written to {RESULTS} (holdout from {panel.index[-s1t.HOLDOUT_ROWS].date()} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, _ = s1t.load_panel()
    start = panel.index[-s1t.HOLDOUT_ROWS]
    daily, _, _ = runs(panel, volumes, holdout=0)
    daily = {k: v.loc[start:] for k, v in daily.items() if k in ("S0", H1_NAME, H2_NAME)}
    books = pd.DataFrame([{"book": k, **s1t.measure(v, panel)} for k, v in daily.items()])
    books["correlation_with_s0"] = [float(v.corr(daily["S0"])) for v in daily.values()]
    books.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame(daily).to_csv(RESULTS / "holdout_daily_returns.csv")
    s0 = books.set_index("book").loc["S0"]
    rows = []
    for hypothesis, name in (("H1", H1_NAME), ("H2", H2_NAME)):
        b = books.set_index("book").loc[name]
        deeper = b["max_drawdown"] < s0["max_drawdown"]
        lagging = b["sharpe_excess"] < s0["sharpe_excess"] - 0.5
        rows.append({"hypothesis": hypothesis, "book": name,
                     "drawdown_vs_s0": b["max_drawdown"] - s0["max_drawdown"],
                     "sharpe_vs_s0": b["sharpe_excess"] - s0["sharpe_excess"],
                     "killed": bool(deeper or lagging)})  # fmt: skip
    pd.DataFrame(rows).to_csv(RESULTS / "holdout_criteria.csv", index=False)
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
