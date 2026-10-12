"""The fund's portfolio backtester (engine/src/backtest.cpp).

    from dv02_engine.backtest import HawkBacktester
    bt = HawkBacktester(initial_value=1_000_000, slippage_bps=1.0, fee_model="ibkr_pro_fixed")
    out = bt.run(prices_df, weights_df)
    out["backtest_results"], out["backtest_positions"], out["backtest_weights"], out["backtest_metrics"]

The same call, frames and numbers as the Rust hawk-backtester it replaces,
except: asset columns come in the weights frame's order (Rust's order was
random); a NaN weight, or a NaN price the backtest would use, is an error
(Rust carried on, and every later value came out NaN); an unknown fee_model
is an error (Rust charged no commission); no weight rows is a ValueError (Rust
panicked); and annualized figures count only the simulated days (Rust also
counted price dates before the first weight date).

prices_df and weights_df are polars or pandas DataFrames, each with a string
"date" column and one Float64 or Int64 column per asset: prices, and target
weights on the dates to rebalance. Dates are year, month, day separated by '-'
or '/' ("2023-01-15", "2023/1/15"). No value may be null (for pandas, NaN is
null). A polars price may be NaN only where the backtest never reads it:
before the first weight date, or for an asset the weights don't name. The
results are polars DataFrames when prices_df is one, pandas otherwise.

The backtest starts on the first weight date. On each price date positions
drift with prices; on the first price date on or after a weight date the book
is rebalanced to those weights, the rest held as cash, with slippage
(slippage_bps of every dollar traded) and commission (fee_model) paid from cash.
"""

from __future__ import annotations

import math
import sys
import time
from typing import Any

import numpy as np

from ._core import backtest as _core

__all__ = ["HawkBacktester"]

RESULT_COLUMNS = (
    "portfolio_value",
    "daily_return",
    "daily_log_return",
    "cumulative_return",
    "cumulative_log_return",
    "drawdown",
    "volume_traded",
    "daily_slippage_cost",
    "daily_commission_cost",
)


class HawkBacktester:
    """A backtester with a starting value and costs; run() takes prices and weights."""

    def __init__(self, initial_value=None, slippage_bps=None, fee_model=None):
        self.initial_value = 1_000_000.0 if initial_value is None else _number(initial_value, "initial_value")
        slippage = 0.0 if slippage_bps is None else _number(slippage_bps, "slippage_bps")
        self.slippage_bps = slippage if slippage > 0.0 else 0.0  # negative or NaN means none
        if fee_model is not None and not isinstance(fee_model, str):
            raise TypeError(f"fee_model must be a string or None, not {type(fee_model).__name__}")
        self.fee_model = fee_model
        self._fee_model = _core.FeeModel.NONE if fee_model is None else _core.fee_model_from_string(fee_model)

    def run(self, prices_df, weights_df) -> dict[str, Any]:
        """Backtest the weights over the prices.

        Returns {"backtest_results", "backtest_positions", "backtest_weights",
        "backtest_metrics"}. Raises ValueError for frames it can't read.
        """
        started = time.perf_counter()
        polars_out = _library(prices_df) == "polars"
        price_days, prices, price_assets = _read(prices_df, "price")
        weight_days, weights, weight_assets = _read(weights_df, "weight")
        if len(weight_days) == 0:
            raise ValueError("Cannot determine start date without weight events.")
        _check_nan(price_days, prices, price_assets, weight_days, weights, weight_assets)
        if "cash" in weight_assets:
            raise RuntimeError("Error running backtest: an asset named 'cash' clashes with the cash column")
        parsed = time.perf_counter()

        out = _core.run(
            price_days,
            prices,
            price_assets,
            weight_days,
            weights,
            weight_assets,
            self.initial_value,
            self.slippage_bps,
            self._fee_model,
        )
        frames = _polars_frames(out) if polars_out else _pandas_frames(out)
        simulated = time.perf_counter()

        num_prices = len(price_days)
        simulation_seconds = simulated - parsed
        metrics = {name: float(value) for name, value in out["metrics"].items()}
        metrics["num_price_points"] = float(num_prices)
        metrics["num_weight_events"] = float(len(weight_days))
        metrics["parsing_time_ms"] = _whole_ms(parsed - started)
        metrics["simulation_time_ms"] = _whole_ms(simulation_seconds)
        metrics["total_time_ms"] = _whole_ms(simulated - started)
        metrics["simulation_speed_dates_per_sec"] = _ratio(num_prices, simulation_seconds)

        results, positions, weights_frame = frames
        return {
            "backtest_results": results,
            "backtest_positions": positions,
            "backtest_weights": weights_frame,
            "backtest_metrics": _metrics_frame(metrics, polars_out),
        }


# ── reading frames ────────────────────────────────────────────────────


def _number(value, name: str) -> float:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a number, not {type(value).__name__}")
    return float(value)


def _library(df) -> str:
    # a DataFrame's library is already imported; a LazyFrame or a Series is not a DataFrame
    for name in ("polars", "pandas"):
        library = sys.modules.get(name)
        if library is not None and isinstance(df, library.DataFrame):
            return name
    raise TypeError(f"expected a polars or pandas DataFrame, not {type(df).__name__}")


def _read(df, kind: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(days since 1970-01-01, dates x assets float64 matrix, asset names), with the checks
    the Rust input handler made, in its order. kind is "price" or "weight"."""
    try:
        if _library(df) == "polars":
            dates, values, assets = _read_polars(df, kind)
        else:
            dates, values, assets = _read_pandas(df, kind)
        days = _core.parse_dates(dates)
    except ValueError as e:
        raise ValueError(f"Error parsing {kind} data: {e}") from None
    return days, values, assets


def _read_polars(df, kind: str):
    import polars as pl

    schema = df.schema
    if "date" not in schema:
        raise ValueError('no column named "date"')
    if schema["date"] != pl.String:
        raise ValueError("Date column must be string type")
    if df.width < 2:
        raise ValueError(f"DataFrame must have at least one {kind} column besides date")
    assets = [c for c in schema if c != "date"]
    for c in assets:
        if schema[c] not in (pl.Float64, pl.Int64):
            raise ValueError(f"Column {c} must be numeric type")
    if sum(df.null_count().row(0)) > 0:
        raise ValueError("DataFrame contains null values")
    # NaN is not null to polars, so it gets through here; _check_nan decides
    values = np.ascontiguousarray(df.drop("date").cast(pl.Float64).to_numpy(), dtype=np.float64)
    return df["date"].to_list(), values, assets


def _read_pandas(df, kind: str):
    import pandas as pd

    names = [str(c) for c in df.columns]
    if len(set(names)) != len(names):
        raise ValueError("DataFrame has two columns with the same name")
    if "date" not in names:
        raise ValueError('no column named "date"')
    date = df.iloc[:, names.index("date")]
    if not pd.api.types.is_string_dtype(date):
        raise ValueError("Date column must be string type")
    if len(names) < 2:
        raise ValueError(f"DataFrame must have at least one {kind} column besides date")
    columns = [i for i, name in enumerate(names) if name != "date"]
    for i in columns:
        # numpy, nullable and pyarrow-backed float64 and int64
        dtype = df.dtypes.iloc[i]
        if getattr(dtype, "numpy_dtype", dtype) not in (np.float64, np.int64):
            raise ValueError(f"Column {names[i]} must be numeric type")
    if df.isna().to_numpy().any():
        raise ValueError("DataFrame contains null values")
    values = df.iloc[:, columns].to_numpy(dtype=np.float64)
    return date.tolist(), np.ascontiguousarray(values), [names[i] for i in columns]


def _check_nan(price_days, prices, price_assets, weight_days, weights, weight_assets) -> None:
    """A NaN weight, or a NaN price from the first weight date on for an asset the weights name,
    is a ValueError: Rust used it, and every later value came out NaN. Other NaN prices Rust never
    read, so they are left alone. (The C++ side would hold a NaN-priced position at its last mark.)"""

    def first_date(days, rows) -> str:
        return str(np.datetime64(int(days[rows].min()), "D"))

    in_backtest = price_days >= weight_days.min()
    named = set(weight_assets)
    for j, asset in enumerate(price_assets):
        rows = in_backtest & np.isnan(prices[:, j])
        if asset in named and rows.any():
            raise ValueError(
                f"Error parsing price data: Column {asset} has a NaN price on {first_date(price_days, rows)}"
            )
    for j, asset in enumerate(weight_assets):
        rows = np.isnan(weights[:, j])
        if rows.any():
            raise ValueError(
                f"Error parsing weight data: Column {asset} has a NaN weight on {first_date(weight_days, rows)}"
            )


# ── writing frames ────────────────────────────────────────────────────


def _columns(out: dict) -> tuple[dict, dict, dict]:
    """The three daily frames as {name: column}, in the Rust backtester's column order."""
    assets = out["assets"]
    results = {"date": out["dates"], **{c: out[c] for c in RESULT_COLUMNS}}
    positions = {"date": out["dates"], **{a: out["positions"][:, j] for j, a in enumerate(assets)}, "cash": out["cash"]}
    weights = {
        "date": out["dates"],
        **{a: out["weights"][:, j] for j, a in enumerate(assets)},
        "cash": out["cash_weight"],
    }
    return results, positions, weights


def _polars_frames(out: dict):
    import polars as pl

    def frame(columns: dict):
        return pl.DataFrame(
            [pl.Series("date", columns.pop("date"), dtype=pl.String)]
            + [pl.Series(name, values, dtype=pl.Float64) for name, values in columns.items()]
        )

    return tuple(frame(c) for c in _columns(out))


def _pandas_frames(out: dict):
    import pandas as pd

    return tuple(pd.DataFrame(c) for c in _columns(out))


def _metrics_frame(metrics: dict[str, float], polars_out: bool):
    names, values = list(metrics), np.array(list(metrics.values()), dtype=np.float64)
    if polars_out:
        import polars as pl

        return pl.DataFrame([pl.Series("metric", names, dtype=pl.String), pl.Series("value", values)])
    import pandas as pd

    return pd.DataFrame({"metric": names, "value": values})


def _whole_ms(seconds: float) -> float:
    return float(math.floor(seconds * 1000))


def _ratio(count: int, seconds: float) -> float:
    if seconds > 0:
        return count / seconds
    return math.inf if count else math.nan
