"""
s1_trend.py — S1, cross-asset trend on ETF proxies, through the research gate.
HYPOTHESIS.md has the rules, costs, split and criteria; REPORT.md the verdict.

    python s1_trend.py                      # the walk-forward, then the holdout: every number
    python s1_trend.py --stage walkforward  # out of sample only; the holdout never reaches the backtester
    python s1_trend.py --stage holdout      # the holdout

Use the repo's .venv Python (it has the C++ module dv02_engine). Reads
trading-engine/data/market/cleaned (read-only) and writes results/.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from dv02_engine.costs import CostModel
from dv02_engine.walkforward import deflated_sharpe_ratio, sharpe_standard_error, walk_forward

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # trading-engine
PRICES = ROOT / "data" / "market" / "cleaned" / "prices"
TARGETS = ROOT / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet"
RESULTS = HERE / "results"

RISKY = ["SPY", "VEA", "VWO", "TLT", "IEI", "GLD", "SLV", "USO", "UNG", "VNQ"]
CASH = "BIL"
START = "2005-01-03"
FIRST_DECISION = "2008-01-02"
HOLDOUT_ROWS = 756
TEST_ROWS = 252
REBALANCE_EVERY = 21
LOOKBACKS = {"3m": (63,), "6m": (126,), "12m": (252,), "blend": (63, 126, 252)}
HISTORY_ROWS = 253  # closes an asset needs up to t to be traded
VOL_ROWS = 63
BUDGET = 0.995  # gross with every eligible asset at full signal; the rest is a cash buffer
NAME_CAP = 0.20
VOL_CEILING = 0.10
TRADING_DAYS = 252
PRIMARY = ("blend", "long_short")
VARIANTS = [(lb, d) for lb in ("3m", "6m", "12m", "blend") for d in ("long_short", "long_flat")]
# UNG's closes on disk miss 28 days from 2010-06-03 to 2010-08-04. The panel
# carries its last close over them (so S0's own targets can be valued), but S1
# counts its history only from the first close after them.
FIRST_HISTORY = {"UNG": pd.Timestamp("2010-08-05")}
COSTS = CostModel(
    half_spread_bps=2.0,
    impact_k=0.5,
    borrow_bps=25.0,
    borrow_bps_by_symbol={"USO": 100.0, "UNG": 100.0, "SLV": 100.0},
    cash_interest_bps=0.0,  # idle money sits in BIL, which earns the T-bill rate
)
REGIMES = {
    "2008": ("2008-01-01", "2008-12-31"),
    "2020": ("2020-01-01", "2020-12-31"),
    "2020 crash (Feb 19 to Mar 23)": ("2020-02-19", "2020-03-23"),
    "2022": ("2022-01-01", "2022-12-31"),
}


# ── data ──────────────────────────────────────────────────────────────


def load_panel(approx_dividends: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(closes, volumes, data checks): one column per symbol from START.

    Closes are raw up to each fund's first total-return close and IBKR's
    dividend-adjusted closes after it, chained there. approx_dividends adds
    each fund's dividend yield, measured where the two series overlap, to its
    raw daily returns before that date (BIL excepted: its income tracks the
    T-bill rate, which was near 0 for most of 2009 to 2015)."""
    master = pd.read_parquet(PRICES / "etf_prices_master.parquet")
    master["date"] = pd.to_datetime(master["date"])
    raw = master.pivot(index="date", columns="symbol", values="close").sort_index()
    volumes = master.pivot(index="date", columns="symbol", values="volume").sort_index()
    tr = pd.read_parquet(PRICES / "etf_total_return_closes.parquet")
    tr["date"] = pd.to_datetime(tr["date"])
    adjusted = tr.pivot(index="date", columns="symbol", values="adj_close").sort_index()

    closes, checks = {}, []
    for symbol in raw.columns:
        x = raw[symbol]
        first = x.first_valid_index()
        live = x.loc[first:]
        gaps = int(live.isna().sum())
        x = pd.concat([x.loc[: first - pd.Timedelta(days=1)], live.ffill()])
        moves = x.pct_change(fill_method=None).dropna()
        a = adjusted[symbol].dropna() if symbol in adjusted.columns else pd.Series(dtype=float)
        a = a[a.index.isin(x.dropna().index)]
        chained, overlap_yield, tr_from = x.copy(), math.nan, None
        if len(a):
            t0 = a.index[0]
            tr_from = t0.date().isoformat()
            after = x.index >= t0
            ratio = a.reindex(x.index[after]).ffill() / a.loc[t0]
            chained[after] = x.loc[t0] * ratio.to_numpy()
            years = (a.index[-1] - t0).days / 365.25
            price_growth = x.loc[a.index[-1]] / x.loc[t0]
            overlap_yield = float((a.iloc[-1] / a.iloc[0] / price_growth) ** (1 / years) - 1) if years > 1 else math.nan
            if approx_dividends and symbol != CASH and overlap_yield > 0:
                daily = (1 + overlap_yield) ** (1 / TRADING_DAYS) - 1
                before = x.loc[:t0]
                r = before.pct_change(fill_method=None)
                grown = ((1 + r) * (1 + daily)).fillna(1.0).cumprod()
                grown[before.isna()] = np.nan
                chained.loc[:t0] = grown / grown.loc[t0] * x.loc[t0]
        closes[symbol] = chained
        checks.append(
            {
                "symbol": symbol,
                "first_close": first.date().isoformat(),
                "missing_days_carried": gaps,
                "moves_over_25pct": "; ".join(f"{d.date()} {v:+.1%}" for d, v in moves[moves.abs() > 0.25].items()),
                "total_return_closes_from": tr_from,
                "dividend_yield_on_overlap": overlap_yield,
            }
        )
    panel = pd.DataFrame(closes).loc[START:]
    volumes = volumes.reindex(index=panel.index, columns=panel.columns)
    return panel, volumes, pd.DataFrame(checks)


def s0_targets() -> pd.DataFrame:
    t = pd.read_parquet(TARGETS)
    t["rebalance_date"] = pd.to_datetime(t["rebalance_date"])
    return t


# ── strategies ────────────────────────────────────────────────────────


def s1(lookback: str, direction: str):
    """S1's rule (HYPOTHESIS.md): sign of the lookback returns, inverse-vol sizing to
    a 99.5% budget, a 20% name cap, a 10% vol ceiling that only scales down, BIL for
    the rest."""
    lookbacks = LOOKBACKS[lookback]
    long_flat = direction == "long_flat"

    def strategy(history: pd.DataFrame):
        tail = history.iloc[-HISTORY_ROWS:]
        if len(tail) < HISTORY_ROWS:
            return None
        eligible = [
            a for a in RISKY
            if tail[a].notna().all() and tail.index[0] >= FIRST_HISTORY.get(a, tail.index[0])
        ]  # fmt: skip
        cash_ok = bool(np.isfinite(history[CASH].iloc[-1]))
        if not eligible:
            return {CASH: BUDGET} if cash_ok else None
        last = tail.iloc[-1]
        signal = {}
        for a in eligible:
            signs = [float(np.sign(last[a] / tail[a].iloc[-1 - lb] - 1.0)) for lb in lookbacks]
            if long_flat:
                signs = [max(s, 0.0) for s in signs]
            signal[a] = float(np.mean(signs))
        returns = tail[eligible].iloc[-(VOL_ROWS + 1) :].pct_change(fill_method=None).iloc[1:]
        sigma = returns.std(ddof=1) * math.sqrt(TRADING_DAYS)
        sigma = sigma[sigma > 0]
        if sigma.empty:
            return {CASH: BUDGET} if cash_ok else None
        inverse = 1.0 / sigma
        w = pd.Series({a: signal[a] * inverse[a] for a in sigma.index}) * (BUDGET / inverse.sum())
        w = w.clip(-NAME_CAP, NAME_CAP)
        cov = returns[sigma.index].cov().to_numpy() * TRADING_DAYS
        book_vol = math.sqrt(max(float(w.to_numpy() @ cov @ w.to_numpy()), 0.0))
        if book_vol > VOL_CEILING:
            w = w * (VOL_CEILING / book_vol)
        weights = {a: float(v) for a, v in w.items() if v != 0.0}
        if cash_ok:
            weights[CASH] = BUDGET - float(w.abs().sum())
        return weights

    return strategy


def s0(targets: pd.DataFrame):
    """The live model's monthly targets, scaled to the same 99.5% budget."""
    by_date = {
        d: (g.set_index("symbol")["target_weight"] * BUDGET).to_dict()
        for d, g in targets.groupby("rebalance_date")
    }

    def strategy(history: pd.DataFrame):
        return by_date.get(history.index[-1])

    return strategy


# ── running and measuring ─────────────────────────────────────────────


def run(panel, volumes, strategy, *, rebalance_every: int, holdout: int, n_trials: int):
    first = int(panel.index.searchsorted(pd.Timestamp(FIRST_DECISION)))
    return walk_forward(
        panel,
        strategy,
        train=first,
        test=TEST_ROWS,
        costs=COSTS,
        n_trials=n_trials,
        holdout=holdout,
        rebalance_every=rebalance_every,
        lag=1,
        volumes=volumes,
        mandate=True,
    )


def excess_returns(daily: pd.Series, panel: pd.DataFrame) -> pd.Series:
    """Daily return over BIL's, the T-bill rate the money earns anyway."""
    bil = panel[CASH].pct_change(fill_method=None).reindex(daily.index).fillna(0.0)
    return daily - bil


def measure(daily: pd.Series, panel: pd.DataFrame) -> dict[str, float]:
    x = excess_returns(daily, panel)
    years = len(daily) / TRADING_DAYS
    sharpe = float(x.mean() / x.std(ddof=1) * math.sqrt(TRADING_DAYS))
    growth = (1 + daily).cumprod()
    return {
        "start": daily.index[0].date().isoformat(),
        "end": daily.index[-1].date().isoformat(),
        "years": years,
        "sharpe_excess": sharpe,
        "sharpe_excess_se": sharpe_standard_error(sharpe, years),
        "sharpe_raw": float(daily.mean() / daily.std(ddof=1) * math.sqrt(TRADING_DAYS)),
        "annual_return": float(growth.iloc[-1] ** (1 / years) - 1),
        "annual_excess_return": float(x.mean() * TRADING_DAYS),
        "annual_volatility": float(daily.std(ddof=1) * math.sqrt(TRADING_DAYS)),
        "max_drawdown": float((growth / growth.cummax() - 1).min()),
    }


def deflated(x: pd.Series, n_trials: int, trial_sharpes: list[float]) -> tuple[float, float]:
    """The deflated Sharpe ratio of excess returns x and its threshold, annualized."""
    r = x.to_numpy()
    centred = r - r.mean()
    m2 = float(np.mean(centred**2))
    skew = float(np.mean(centred**3)) / m2**1.5
    kurt = float(np.mean(centred**4)) / m2**2
    variance = float(np.var(trial_sharpes, ddof=1)) / TRADING_DAYS
    dsr, threshold = deflated_sharpe_ratio(r.mean() / r.std(ddof=1), len(r), n_trials, variance, skew, kurt)
    return dsr, threshold * math.sqrt(TRADING_DAYS)


def block_bootstrap(x: pd.Series, block: int = 63, n: int = 2_000, seed: int = 21) -> np.ndarray:
    """Annualized Sharpe ratios of moving-block resamples of x."""
    r = x.to_numpy()
    rng = np.random.default_rng(seed)
    blocks = math.ceil(len(r) / block)
    out = np.empty(n)
    for i in range(n):
        starts = rng.integers(0, len(r) - block + 1, size=blocks)
        sample = np.concatenate([r[s : s + block] for s in starts])[: len(r)]
        out[i] = sample.mean() / sample.std(ddof=1) * math.sqrt(TRADING_DAYS)
    return out


def period_return(daily: pd.Series, start: str, end: str) -> float:
    block = daily.loc[start:end]
    return float((1 + block).prod() - 1) if len(block) else math.nan


def book_exposure(events: pd.DataFrame) -> dict[str, float]:
    risky = [c for c in events.columns if c in RISKY]
    weights = events[risky]
    return {
        "avg_risky_gross": float(weights.abs().sum(axis=1).mean()),
        "avg_long": float(weights.clip(lower=0).sum(axis=1).mean()),
        "avg_short": float(-weights.clip(upper=0).sum(axis=1).mean()),
        "avg_bil": float(events[CASH].mean()) if CASH in events.columns else 0.0,
    }


# ── stages ────────────────────────────────────────────────────────────


def walkforward_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, checks = load_panel()
    checks.to_csv(RESULTS / "data_checks.csv", index=False)
    holdout_start = panel.index[-HOLDOUT_ROWS].date().isoformat()

    rows, daily = [], {}
    for lookback, direction in VARIANTS:
        wf = run(panel, volumes, s1(lookback, direction), rebalance_every=REBALANCE_EVERY,
                 holdout=HOLDOUT_ROWS, n_trials=len(VARIANTS))  # fmt: skip
        name = f"{lookback}_{direction}"
        daily[name] = wf.equity["daily_return"]
        c = wf.combined
        rows.append({
            "variant": name,
            **measure(wf.equity["daily_return"], panel),
            "turnover": c["turnover"],
            "cost_drag": c["cost_drag"],
            "total_cost": c["total_cost"],
            "mandate_breach_days": c["mandate_breach_days"],
            "max_gross_exposure": c["max_gross_exposure"],
            **book_exposure(wf.events),
        })  # fmt: skip
        if (lookback, direction) == PRIMARY:
            wf.folds.to_csv(RESULTS / "walkforward_primary_folds.csv", index=False)
            wf.events.to_csv(RESULTS / "walkforward_primary_weights.csv", index=False)
    variants = pd.DataFrame(rows)

    trial_sharpes = variants["sharpe_excess"].tolist()
    primary = f"{PRIMARY[0]}_{PRIMARY[1]}"
    x_primary = excess_returns(daily[primary], panel)
    dsr = {}
    for name in variants["variant"]:
        dsr[name] = deflated(excess_returns(daily[name], panel), len(VARIANTS), trial_sharpes)
    variants["deflated_sharpe"] = variants["variant"].map(lambda n: dsr[n][0])
    variants["deflated_sharpe_threshold"] = variants["variant"].map(lambda n: dsr[n][1])
    variants.to_csv(RESULTS / "walkforward_variants.csv", index=False)

    # S0, the live model, over the same out-of-sample span, and the 50/50 book
    s0_wf = run(panel, volumes, s0(s0_targets()), rebalance_every=1, holdout=HOLDOUT_ROWS, n_trials=1)
    # the books on their common days: S0's first trade follows its first month-end target
    common = daily[primary].index.intersection(s0_wf.equity.index)
    r_s0 = s0_wf.equity["daily_return"].loc[common]
    r_s1 = daily[primary].loc[common]
    r_mix = 0.5 * r_s0 + 0.5 * r_s1
    books = pd.DataFrame([
        {"book": "S1 primary", **measure(r_s1, panel)},
        {"book": "S0 (live model)", **measure(r_s0, panel), "turnover": s0_wf.combined["turnover"],
         "cost_drag": s0_wf.combined["cost_drag"], "mandate_breach_days": s0_wf.combined["mandate_breach_days"]},
        {"book": "50/50 S0+S1", **measure(r_mix, panel)},
    ])  # fmt: skip
    books.to_csv(RESULTS / "walkforward_books.csv", index=False)
    pd.DataFrame({"s1_primary": r_s1, "s0": r_s0, "blend_50_50": r_mix,
                  "bil": panel[CASH].pct_change(fill_method=None).reindex(r_s1.index)}).to_csv(
        RESULTS / "walkforward_daily_returns.csv")  # fmt: skip

    x_s0 = excess_returns(r_s0, panel)
    monthly = pd.DataFrame({"s1": r_s1, "s0": r_s0}).add(1).resample("ME").prod().sub(1)
    worst = monthly["s0"] <= monthly["s0"].quantile(0.10)
    pd.DataFrame([
        {"measure": "daily return correlation, S1 vs S0", "value": float(r_s1.corr(r_s0))},
        {"measure": "daily excess return correlation, S1 vs S0", "value": float(x_primary.corr(x_s0))},
        {"measure": "monthly return correlation, S1 vs S0", "value": float(monthly["s1"].corr(monthly["s0"]))},
        {"measure": "S1's mean monthly return in S0's worst 10% of months", "value": float(monthly.loc[worst, "s1"].mean())},
        {"measure": "S0's mean monthly return in its worst 10% of months", "value": float(monthly.loc[worst, "s0"].mean())},
    ]).to_csv(RESULTS / "walkforward_correlation.csv", index=False)  # fmt: skip

    regimes = []
    for label, (a, b) in REGIMES.items():
        regimes.append({"period": label, "s1_primary": period_return(r_s1, a, b), "s0": period_return(r_s0, a, b),
                        "blend_50_50": period_return(r_mix, a, b),
                        **{n: period_return(daily[n], a, b) for n in variants["variant"] if n != primary}})  # fmt: skip
    pd.DataFrame(regimes).to_csv(RESULTS / "walkforward_regimes.csv", index=False)

    boot = block_bootstrap(x_primary)
    pd.DataFrame([{"percentile": p, "sharpe_excess": float(np.percentile(boot, p))} for p in (5, 25, 50, 75, 95)]).to_csv(
        RESULTS / "walkforward_bootstrap.csv", index=False)  # fmt: skip

    # the same primary on the panel with approximate dividends before 2016-10 (a data check)
    panel_d, volumes_d, _ = load_panel(approx_dividends=True)
    wf_d = run(panel_d, volumes_d, s1(*PRIMARY), rebalance_every=REBALANCE_EVERY, holdout=HOLDOUT_ROWS, n_trials=8)
    s0_d = run(panel_d, volumes_d, s0(s0_targets()), rebalance_every=1, holdout=HOLDOUT_ROWS, n_trials=1)
    pd.DataFrame([
        {"book": "S1 primary, approximate dividends before 2016-10", **measure(wf_d.equity["daily_return"], panel_d)},
        {"book": "S0, approximate dividends before 2016-10", **measure(s0_d.equity["daily_return"], panel_d)},
    ]).to_csv(RESULTS / "walkforward_dividend_sensitivity.csv", index=False)  # fmt: skip

    p = variants.set_index("variant").loc[primary]
    s0_row = books.set_index("book").loc["S0 (live model)"]
    mix_row = books.set_index("book").loc["50/50 S0+S1"]
    long_short = variants[variants["variant"].str.endswith("long_short")]
    criteria = [
        ("1. excess Sharpe >= 0.40", p["sharpe_excess"], p["sharpe_excess"] >= 0.40),
        ("2. deflated Sharpe >= 0.90 (n_trials 8)", p["deflated_sharpe"], p["deflated_sharpe"] >= 0.90),
        ("3. block bootstrap 5th percentile > 0", float(np.percentile(boot, 5)), float(np.percentile(boot, 5)) > 0),
        ("4. every long/short lookback > 0", float(long_short["sharpe_excess"].min()),
         bool((long_short["sharpe_excess"] > 0).all())),
        ("5. max drawdown no worse than -20%", p["max_drawdown"], p["max_drawdown"] >= -0.20),
        ("6a. positive in 2008", regimes[0]["s1_primary"], regimes[0]["s1_primary"] > 0),
        ("6b. positive in 2022", regimes[3]["s1_primary"], regimes[3]["s1_primary"] > 0),
        ("7a. correlation with S0 <= 0.50", float(r_s1.corr(r_s0)), float(r_s1.corr(r_s0)) <= 0.50),
        ("7b. 50/50 book's excess Sharpe > S0's", mix_row["sharpe_excess"] - s0_row["sharpe_excess"],
         mix_row["sharpe_excess"] > s0_row["sharpe_excess"]),
        ("8. cost drag <= 1% a year", p["cost_drag"], p["cost_drag"] <= 0.01),
    ]  # fmt: skip
    pd.DataFrame(criteria, columns=["criterion", "value", "pass"]).to_csv(RESULTS / "walkforward_criteria.csv", index=False)
    print(f"walk-forward written to {RESULTS} (holdout from {holdout_start} untouched)")


def holdout_stage() -> None:
    RESULTS.mkdir(exist_ok=True)
    panel, volumes, _ = load_panel()
    start = panel.index[-HOLDOUT_ROWS]
    wf = run(panel, volumes, s1(*PRIMARY), rebalance_every=REBALANCE_EVERY, holdout=0, n_trials=len(VARIANTS))
    s0_wf = run(panel, volumes, s0(s0_targets()), rebalance_every=1, holdout=0, n_trials=1)
    common = wf.equity.loc[start:].index.intersection(s0_wf.equity.index)
    r_s1 = wf.equity["daily_return"].loc[common]
    r_s0 = s0_wf.equity["daily_return"].loc[common]
    r_mix = 0.5 * r_s0 + 0.5 * r_s1
    books = pd.DataFrame([
        {"book": "S1 primary", **measure(r_s1, panel)},
        {"book": "S0 (live model)", **measure(r_s0, panel)},
        {"book": "50/50 S0+S1", **measure(r_mix, panel)},
    ])  # fmt: skip
    books["correlation_with_s0"] = [float(r_s1.corr(r_s0)), 1.0, float(r_mix.corr(r_s0))]
    books.to_csv(RESULTS / "holdout_books.csv", index=False)
    pd.DataFrame({"s1_primary": r_s1, "s0": r_s0, "blend_50_50": r_mix}).to_csv(RESULTS / "holdout_daily_returns.csv")
    p = books.set_index("book").loc["S1 primary"]
    killed = p["sharpe_excess"] < -0.5 or p["max_drawdown"] < -0.15
    pd.DataFrame([
        {"criterion": "holdout excess Sharpe >= -0.5", "value": p["sharpe_excess"], "pass": p["sharpe_excess"] >= -0.5},
        {"criterion": "holdout max drawdown no worse than -15%", "value": p["max_drawdown"], "pass": p["max_drawdown"] >= -0.15},
        {"criterion": "holdout kills it", "value": float(killed), "pass": not killed},
    ]).to_csv(RESULTS / "holdout_criteria.csv", index=False)  # fmt: skip
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
