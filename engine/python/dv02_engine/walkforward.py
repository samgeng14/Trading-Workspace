"""Walk-forward out-of-sample tests over the C++ backtester (docs/PLAN.md Section 9, the research gate).

    from dv02_engine.costs import CostModel
    from dv02_engine.walkforward import walk_forward

    def momentum(history):                       # rows up to and including the decision date
        r = history.iloc[-1] / history.iloc[-253] - 1
        top = r.nlargest(5).index
        return {a: 0.19 for a in top}            # the whole target book; anything left out is sold

    wf = walk_forward(prices, momentum, train=504, test=126, rebalance_every=21,
                      costs=CostModel(), n_trials=12)
    wf.folds        # one row per fold: Sharpe, its standard error, turnover, costs, drawdown
    wf.combined     # the stitched out-of-sample record, with the deflated Sharpe ratio
    wf.equity       # the stitched out-of-sample equity, one row per day

How it runs:

1. The panel's rows are split into folds: a training window, then a test block
   of `test` rows. Blocks follow one another without overlap; the training
   window grows from the first row ("expanding") or keeps `train` rows
   ("rolling"). The `gap` rows between them are an embargo: `fit` never sees
   them and no decision is made on them (the strategy's history still runs
   through them, as past data). The last `holdout` rows are never touched: no
   decision, no trade, no statistic, and their prices and volumes never reach
   the backtester.
2. In each fold, `fit(train_history)` (optional) runs once on the training
   rows only, and `strategy(history)` (or `strategy(history, params)`) runs on
   every `rebalance_every`-th row of the test block, with `history` holding
   the panel's rows up to and including that row and nothing after it.
3. Weights decided on row t trade at the close `lag` price dates later
   (default 1: the next close, the conservative stand-in for an order that
   missed the 3:35 pm MOC cutoff and fills the next morning; lag=0 trades at
   the close the strategy saw, the on-time MOC case, about 25 minutes
   optimistic). A trade that would land in the holdout or past the panel is
   dropped.
4. Every fold's weights go through the C++ backtester in one run over the
   out-of-sample span, with the cost model and the mandate check, so a fold
   boundary doesn't liquidate and rebuy the book. Fold k's out-of-sample days
   run from its first trade date to the next fold's; the stitched equity is
   that run from the first test date on.

No look-ahead: the strategy only ever receives rows up to t, and `fit` only
the training rows; a weight that is NaN (what a strategy reading the next row
through `shift(-1)` or a centred window gets on the last row) is an error. A
strategy that reaches the whole panel some other way (a closure over the full
frame, a signal computed on all of it beforehand) can see the future, and no
harness can tell: compute everything inside `strategy` and `fit` from what
they are given.

Statistics, from daily returns (252 a year, risk-free 0):
- sharpe: mean / sample std x sqrt(252); sharpe_se = sqrt((1 + SR^2 / 2) / T),
  T in years (PLAN Section 9).
- deflated_sharpe (Bailey & Lopez de Prado 2014): the probability that the
  true Sharpe beats the best of n_trials skill-less variants, allowing for
  the returns' skewness and kurtosis. The variance of the trials' Sharpe
  ratios comes from `trial_sharpes` when given; otherwise from estimation
  noise alone, 1 / (T - 1) per day.
- turnover: dollars traded a year / (2 x average value), as the backtester's
  metric; cost_drag: total cost a year / average value.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from ._core import backtest as _core_backtest
from .backtest import HawkBacktester, Mandate
from .costs import CostModel

__all__ = [
    "Fold",
    "WalkForwardResult",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "make_folds",
    "probabilistic_sharpe_ratio",
    "sharpe_standard_error",
    "walk_forward",
]

TRADING_DAYS = 252
EULER_GAMMA = 0.5772156649015329
_NORMAL = NormalDist()
COST_PARTS = ("commission", "spread", "impact", "borrow", "roll")


# ── folds ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fold:
    """One walk-forward fold, as panel row positions (ranges) and dates."""

    index: int
    train: range  # rows fit() sees
    test: range  # rows the strategy decides on
    train_start: str
    train_end: str
    test_start: str
    test_end: str


def _count(name: str, value: Any, least: int) -> None:
    """ValueError unless value is an integer (not a bool) of at least `least`."""
    if not isinstance(value, (int, np.integer)) or isinstance(value, bool) or value < least:
        raise ValueError(f"{name} must be an integer >= {least}, not {value!r}")


def make_folds(
    dates: Sequence[Any], *, train: int, test: int, mode: str = "expanding", gap: int = 0, holdout: int = 0
) -> list[Fold]:
    """Split rows 0..len(dates) - 1 into walk-forward folds.

    The first test block starts at row train + gap; each block is `test` rows
    (the last may be shorter) and ends where the next begins. The training
    window ends `gap` rows before its test block and starts at row 0
    ("expanding") or `train` rows earlier ("rolling"). The last `holdout`
    rows are in no fold.
    """
    for name, value, least in (("train", train, 1), ("test", test, 1), ("gap", gap, 0), ("holdout", holdout, 0)):
        _count(name, value, least)
    if mode not in ("expanding", "rolling"):
        raise ValueError(f"mode must be 'expanding' or 'rolling', not {mode!r}")
    end = len(dates) - holdout
    first = train + gap
    if first >= end:
        raise ValueError(
            f"{len(dates)} rows leave no test block: train {train} + gap {gap} + holdout {holdout} use them all"
        )
    labels = [_label(d) for d in dates]
    folds = []
    start = first
    while start < end:
        stop = min(start + test, end)
        train_rows = range(0 if mode == "expanding" else start - gap - train, start - gap)
        test_rows = range(start, stop)
        folds.append(
            Fold(
                index=len(folds),
                train=train_rows,
                test=test_rows,
                train_start=labels[train_rows.start],
                train_end=labels[train_rows.stop - 1],
                test_start=labels[test_rows.start],
                test_end=labels[test_rows.stop - 1],
            )
        )
        start = stop
    return folds


# ── statistics ────────────────────────────────────────────────────────


def sharpe_standard_error(sharpe: float, years: float) -> float:
    """The standard error of an annualized Sharpe ratio, sqrt((1 + SR^2 / 2) / T), T in years
    (docs/PLAN.md Section 9). NaN when T isn't positive or SR isn't a number."""
    if not (years > 0) or not math.isfinite(sharpe):
        return math.nan
    return math.sqrt((1.0 + sharpe * sharpe / 2.0) / years)


def expected_max_sharpe(n_trials: int, variance: float) -> float:
    """The expected largest of n_trials Sharpe ratios drawn with mean 0 and this variance
    (Bailey & Lopez de Prado 2014, the 'False Strategy' bound):
    sqrt(V) x ((1 - g) Phi^-1(1 - 1/N) + g Phi^-1(1 - 1/(N e))), g Euler's constant.
    0 for a single trial: nothing was selected."""
    _count("n_trials", n_trials, 1)
    if not (variance >= 0):
        raise ValueError(f"the trials' Sharpe variance must be >= 0, not {variance!r}")
    if n_trials == 1:
        return 0.0
    z = (1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / n_trials) + EULER_GAMMA * _NORMAL.inv_cdf(
        1 - 1 / (n_trials * math.e)
    )
    return math.sqrt(variance) * z


def probabilistic_sharpe_ratio(
    sharpe: float, benchmark: float, n_obs: int, skew: float = 0.0, kurtosis: float = 3.0
) -> float:
    """P(true Sharpe > benchmark) given an estimate from n_obs returns (Bailey & Lopez de Prado
    2012). Per-period Sharpe ratios, not annualized; kurtosis is Pearson's (3 for a normal)."""
    if n_obs < 2 or not math.isfinite(sharpe):
        return math.nan
    spread = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe * sharpe
    if not (spread > 0):
        return math.nan
    return _NORMAL.cdf((sharpe - benchmark) * math.sqrt(n_obs - 1) / math.sqrt(spread))


def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    n_trials: int,
    trials_variance: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> tuple[float, float]:
    """(DSR, SR0): the probabilistic Sharpe ratio against SR0, the Sharpe the best of n_trials
    skill-less variants would show (Bailey & Lopez de Prado 2014). Per-period Sharpe ratios and
    variance, not annualized. trials_variance is the variance of the trials' Sharpe ratios;
    None takes estimation noise alone, 1 / (n_obs - 1)."""
    if n_obs < 2:
        return math.nan, math.nan
    variance = 1.0 / (n_obs - 1) if trials_variance is None else trials_variance
    threshold = expected_max_sharpe(n_trials, variance)
    return probabilistic_sharpe_ratio(sharpe, threshold, n_obs, skew, kurtosis), threshold


def _moments(returns: np.ndarray) -> tuple[float, float, float, float]:
    """(mean, sample std, skewness, Pearson kurtosis); NaN where undefined."""
    n = len(returns)
    if n < 2:
        return (float(returns.mean()) if n else math.nan), math.nan, math.nan, math.nan
    mean = float(returns.mean())
    std = float(returns.std(ddof=1))
    centred = returns - mean
    m2 = float(np.mean(centred**2))
    if m2 == 0:
        return mean, std, math.nan, math.nan
    return mean, std, float(np.mean(centred**3)) / m2**1.5, float(np.mean(centred**4)) / m2**2


def _stats(block: pd.DataFrame, has_costs: bool, has_mandate: bool) -> dict[str, float]:
    """Performance of a run of consecutive days of the backtest results."""
    r = block["daily_return"].to_numpy(dtype=np.float64)
    days = len(r)
    years = days / TRADING_DAYS
    mean, std, _, _ = _moments(r)
    sharpe = mean / std * math.sqrt(TRADING_DAYS) if days >= 2 and std > 0 else math.nan
    growth = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.concatenate(([1.0], growth)))[1:]
    value = block["portfolio_value"].to_numpy(dtype=np.float64)
    average_value = float(value.mean()) if days else math.nan
    final = float(growth[-1]) if days else math.nan
    # A book that lost everything, or more (a levered book the mandate check
    # flags), is -100% a year, not a complex number; its turnover and cost
    # drag, which divide by its average value, are NaN rather than nonsense.
    stats = {
        "days": float(days),
        "sharpe": sharpe,
        "sharpe_se": sharpe_standard_error(sharpe, years),
        "annual_return": final ** (1 / years) - 1 if final > 0 else (-1.0 if days else math.nan),
        "annual_volatility": std * math.sqrt(TRADING_DAYS),
        "max_drawdown": float(np.min(growth / peak - 1)) if days else math.nan,
        "turnover": float(block["volume_traded"].sum()) / years / (2 * average_value)
        if average_value > 0
        else math.nan,
    }
    if has_costs:
        for part in COST_PARTS:
            stats[f"{part}_cost"] = float(block[f"daily_{part}_cost"].sum())
        stats["interest_income"] = float(block["daily_interest_income"].sum())
        stats["total_cost"] = float(block["daily_total_cost"].sum())
        stats["cost_drag"] = stats["total_cost"] / years / average_value if average_value > 0 else math.nan
    if has_mandate:
        stats["mandate_breach_days"] = float(block["mandate_breach"].sum())
        stats["max_gross_exposure"] = float(block["gross_exposure"].max())
    return stats


# ── the harness ───────────────────────────────────────────────────────


@dataclass
class WalkForwardResult:
    """folds: one row per fold. combined: the stitched out-of-sample record. equity: the stitched
    out-of-sample days (date index; portfolio_value, daily_return, fold). events: the weights
    each decision produced (decided, executed, fold, then one column per asset). backtest: the
    C++ backtester's four frames for the whole out-of-sample run. holdout: the first and last
    date held out, or None."""

    folds: pd.DataFrame
    combined: dict[str, float]
    equity: pd.DataFrame
    events: pd.DataFrame
    backtest: dict[str, pd.DataFrame]
    holdout: tuple[str, str] | None


def walk_forward(
    prices: pd.DataFrame,
    strategy: Callable[..., Mapping[str, float] | pd.Series | None],
    *,
    train: int,
    test: int,
    costs: CostModel | None,
    n_trials: int,
    trial_sharpes: Sequence[float] | None = None,
    mode: str = "expanding",
    gap: int = 0,
    holdout: int = 0,
    fit: Callable[[pd.DataFrame], Any] | None = None,
    rebalance_every: int = 1,
    lag: int = 1,
    volumes: pd.DataFrame | None = None,
    mandate: Mandate | bool | None = True,
    initial_value: float = 1_000_000.0,
) -> WalkForwardResult:
    """Walk the strategy forward over the price panel and test it out of sample (module docstring).

    prices: a pandas DataFrame of closes, one column per asset, indexed by date (a
    DatetimeIndex) or with a string "date" column; NaN where an asset has no price.
    strategy(history) returns the whole target book as {asset: weight} (or a Series); an asset
    left out is sold; None keeps the book as it is (no trade). With fit, strategy(history,
    params) gets fit's result for the fold. costs: a CostModel, or None to run without costs
    (the research gate requires them). n_trials: how many variants were tried (an integer >= 1),
    for the deflated Sharpe ratio; trial_sharpes: their annualized Sharpe ratios, if known.
    volumes: shares (or contracts) a day, laid out like prices, for the cost model's impact.
    mandate flags the days outside the no-leverage mandate (True: the default limits).

    Raises ValueError for a panel it can't read, folds that don't fit, or weights it can't
    trade: an asset not in the panel, a weight that isn't finite, or a non-zero weight on an
    asset with no price on the trade date.
    """
    started = time.perf_counter()
    if costs is not None and not isinstance(costs, CostModel):
        raise TypeError(f"costs must be a dv02_engine.costs.CostModel or None, not {type(costs).__name__}")
    for name, value, least in (("rebalance_every", rebalance_every, 1), ("lag", lag, 0), ("n_trials", n_trials, 1)):
        _count(name, value, least)  # n_trials is only read after the run: check it before
    panel = _panel(prices, "prices")
    folds = make_folds(panel.index, train=train, test=test, mode=mode, gap=gap, holdout=holdout)
    end = len(panel) - holdout  # rows from here on are held out
    first_trade = folds[0].test.start + lag
    if first_trade >= end:
        raise ValueError(f"lag {lag} puts the first trade past the last row before the holdout")
    trials_variance = None
    if trial_sharpes is not None:
        sharpes = np.asarray(trial_sharpes, dtype=np.float64)
        if sharpes.ndim != 1 or len(sharpes) < 2 or not np.isfinite(sharpes).all():
            raise ValueError("trial_sharpes must be at least 2 finite annualized Sharpe ratios")
        trials_variance = float(sharpes.var(ddof=1)) / TRADING_DAYS

    # decisions, fold by fold
    assets = list(panel.columns)
    position = {a: j for j, a in enumerate(assets)}
    prices_np = panel.to_numpy(dtype=np.float64)
    events: list[tuple[int, int, int, dict[str, float]]] = []  # decided row, trade row, fold, weights
    for fold in folds:
        params = fit(panel.iloc[fold.train.start : fold.train.stop]) if fit is not None else None
        for row in fold.test[::rebalance_every]:
            history = panel.iloc[: row + 1]
            weights = strategy(history) if fit is None else strategy(history, params)
            if weights is None:
                continue
            trade_row = row + lag
            weights = _check_weights(weights, position, panel.index[row])
            if trade_row >= end:
                continue
            for asset, w in weights.items():
                if w != 0 and not np.isfinite(prices_np[trade_row, position[asset]]):
                    raise ValueError(
                        f"strategy gave {asset} weight {w} on {_label(panel.index[row])}, but it has no price "
                        f"on {_label(panel.index[trade_row])}, the trade date"
                    )
            events.append((row, trade_row, fold.index, weights))

    # one backtest over the out-of-sample span, from the first test date's trade
    named = [a for a in assets if any(a in w for _, _, _, w in events)]
    weight_matrix = np.array([[w.get(a, 0.0) for a in named] for _, _, _, w in events], dtype=np.float64)
    weight_matrix = weight_matrix.reshape(len(events), len(named))
    days = _days(panel.index)
    span = slice(0, end)
    volume_arrays = None
    if volumes is not None:
        if costs is None:
            raise ValueError("volumes are read only by a cost model: pass costs=CostModel(...)")
        volume_panel = _panel(volumes, "volumes")
        # the held-out rows never reach the backtester, as the prices' don't
        volume_panel = volume_panel.loc[volume_panel.index <= panel.index[end - 1]]
        volume_arrays = (
            _days(volume_panel.index),
            np.ascontiguousarray(volume_panel.to_numpy(dtype=np.float64)),
            [str(c) for c in volume_panel.columns],
        )
    backtester = HawkBacktester(initial_value, costs=costs, mandate=mandate)
    out = backtester._simulate(
        (days[span], np.ascontiguousarray(prices_np[span]), [str(a) for a in assets]),
        (
            np.array([days[t] for _, t, _, _ in events], dtype=np.int32),
            np.ascontiguousarray(weight_matrix),
            [str(a) for a in named],
        ),
        volume_arrays,
        False,
        started,
        start_day=int(days[first_trade]),
    )

    # stitch: fold k's out-of-sample days run from its first trade date to the next fold's
    results = out["backtest_results"].copy()
    results.index = pd.DatetimeIndex(pd.to_datetime(results["date"]), name="date")
    bounds = [min(f.test.start + lag, end) for f in folds] + [end]
    fold_of_row = np.full(end, -1, dtype=np.int64)
    for k in range(len(folds)):
        fold_of_row[bounds[k] : bounds[k + 1]] = k
    results["fold"] = fold_of_row[first_trade:end]
    has_costs, has_mandate = costs is not None, mandate not in (None, False)

    rows = []
    for fold in folds:
        block = results[results["fold"] == fold.index]
        stats = _stats(block, has_costs, has_mandate) if len(block) else {"days": 0.0}
        rows.append(
            {
                "fold": fold.index,
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "oos_start": block["date"].iloc[0] if len(block) else None,
                "oos_end": block["date"].iloc[-1] if len(block) else None,
                **stats,
            }
        )
    combined = _stats(results, has_costs, has_mandate)
    r = results["daily_return"].to_numpy(dtype=np.float64)
    mean, std, skew, kurtosis = _moments(r)
    per_day = mean / std if len(r) >= 2 and std > 0 else math.nan
    dsr, threshold = deflated_sharpe_ratio(
        per_day,
        len(r),
        n_trials,
        trials_variance,
        skew if math.isfinite(skew) else 0.0,
        kurtosis if math.isfinite(kurtosis) else 3.0,
    )
    combined.update(
        {
            "deflated_sharpe": dsr,
            "deflated_sharpe_threshold": threshold * math.sqrt(TRADING_DAYS),  # annualized SR0
            "n_trials": float(n_trials),
            "skew": skew,
            "kurtosis": kurtosis,
            "folds": float(len(folds)),
        }
    )

    event_frame = pd.DataFrame(
        {
            "decided": [_label(panel.index[d]) for d, _, _, _ in events],
            "executed": [_label(panel.index[t]) for _, t, _, _ in events],
            "fold": [k for _, _, k, _ in events],
            **{a: weight_matrix[:, j] for j, a in enumerate(named)},
        }
    )
    equity = results[["portfolio_value", "daily_return", "fold"]].copy()
    holdout_dates = (_label(panel.index[end]), _label(panel.index[-1])) if holdout else None
    return WalkForwardResult(pd.DataFrame(rows), combined, equity, event_frame, out, holdout_dates)


# ── helpers ───────────────────────────────────────────────────────────


def _label(date) -> str:
    return pd.Timestamp(date).strftime("%Y-%m-%d")


def _days(index: pd.DatetimeIndex) -> np.ndarray:
    """Days since 1970-01-01 as int32, the backtester's dates."""
    return np.asarray(index.values.astype("datetime64[D]").astype(np.int64), dtype=np.int32)


def _panel(frame: pd.DataFrame, what: str) -> pd.DataFrame:
    """A float64 frame on a sorted, unique DatetimeIndex named "date"."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{what} must be a pandas DataFrame, not {type(frame).__name__}")
    if "date" in frame.columns:
        days = _core_backtest.parse_dates([str(d) for d in frame["date"]])
        frame = frame.drop(columns="date").set_axis(pd.DatetimeIndex(days.astype("datetime64[D]")), axis=0)
    elif not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f'{what} must have a DatetimeIndex or a "date" column')
    if frame.columns.duplicated().any():
        raise ValueError(f"{what} name an asset twice")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError(f"{what} dates must be increasing, each date once")
    for column in frame.columns:
        if not pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]):
            raise ValueError(f"{what} column {column} must be numeric")
    panel = frame.astype(np.float64)
    panel.index = pd.DatetimeIndex(panel.index.normalize(), name="date")
    panel.columns = [str(c) for c in panel.columns]
    return panel


def _check_weights(weights, position: Mapping[str, int], decided) -> dict[str, float]:
    if isinstance(weights, pd.Series):
        weights = weights.to_dict()
    if not isinstance(weights, Mapping):
        raise TypeError(f"strategy must return a mapping or Series of weights, or None, not {type(weights).__name__}")
    checked = {}
    for asset, w in weights.items():
        name = str(asset)
        if name not in position:
            raise ValueError(f"strategy gave a weight to {name} on {_label(decided)}, which is not in the panel")
        value = float(w)
        if not math.isfinite(value):
            raise ValueError(
                f"strategy gave {name} weight {value} on {_label(decided)}. A strategy that reads the row after "
                "its decision date (shift(-1), a centred window) gets NaN there: weights at t may use data up "
                "to t only. So does one still warming up: return None, or 0, until it has the history it needs."
            )
        checked[name] = value
    return checked
