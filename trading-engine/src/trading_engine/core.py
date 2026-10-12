import datetime
import inspect
from typing import List, Union, Dict, Sequence, Optional

import polars as pl
from dv02_engine.backtest import HawkBacktester
from polars import DataFrame, LazyFrame

from common.bundles import RawDataBundle, ModelStateBundle
from common.constants import ProcessingMode
from common.logging import setup_logger
from trading_engine.aggregators import AGGREGATORS
from trading_engine.model_state import FEATURES
from trading_engine.models import MODELS
from trading_engine.optimizers import OPTIMIZERS
from trading_engine.utils import calculate_calendar_lookback, _read_parquet_data_from_gcs

logger = setup_logger(__name__)

pl.enable_string_cache()


def read_data(include_supplemental: bool = False) -> Union[LazyFrame, RawDataBundle]:
    """Read raw data from Parquet in lazy mode."""
    BUCKET = "wsb-hc-qasap-bucket-1"
    raw_records = _read_parquet_data_from_gcs(BUCKET, "hcf/raw_data/universal")

    if not include_supplemental:
        return raw_records

    return RawDataBundle(
        raw_records=raw_records,
        raw_supplemental_records=_read_parquet_data_from_gcs(
            BUCKET, "hcf/raw_data/universal_supplemental"
        ),
    )


def create_model_state(
        lf: Optional[LazyFrame] = None,
        features: Optional[list[str]] = None,
        start_date: Optional[datetime.date] = None,
        end_date: Optional[datetime.date] = None,
        universe: Optional[List[str]] = None,
        registry=None,
        total_lookback_days: Optional[int] = None,
        raw_data_bundle: Optional[RawDataBundle] = None,
        return_bundle: Optional[bool] = None,
) -> tuple[Union[DataFrame, ModelStateBundle], DataFrame]:
    """
    Build model state with warmup lookback and eager feature support.
    Returns (model_state, prices) tuple.
    
    Args:
        lf: Legacy input data (primary raw records only)
        raw_data_bundle: New input bundle containing primary + supplemental records
        features: List of feature names to compute
        start_date: Start date for the model state (after warmup)
        end_date: End date for the model state
        universe: List of tickers to include
        registry: Feature registry (defaults to FEATURES)
        total_lookback_days: Optional total lookback days to extend data before start_date.
                           If provided, this overrides the calculated feature lookback.
                           If None, uses the maximum lookback from features.
        return_bundle: When True returns ModelStateBundle, otherwise legacy DataFrame.
                       Defaults to True when raw_data_bundle is provided, else False.
    """
    if features is None or start_date is None or end_date is None or universe is None:
        raise ValueError("features, start_date, end_date, and universe are required.")

    if raw_data_bundle is not None and lf is not None:
        raise ValueError("Provide either `lf` or `raw_data_bundle`, not both.")

    if raw_data_bundle is None and lf is None:
        raise ValueError("Provide either `lf` or `raw_data_bundle`.")

    if return_bundle is None:
        return_bundle = raw_data_bundle is not None

    raw_records = raw_data_bundle.raw_records if raw_data_bundle is not None else lf
    if raw_records is None:
        raise ValueError("Failed to resolve raw records input.")

    ###
    # Model state 1: Raw records (data associated with a specific Hawk ID)
    ###
    if not registry:
        registry = FEATURES

    lazy_fns, eager_fns = [], []
    for f in features:
        if f not in registry:
            continue
        entry = registry[f]
        (lazy_fns if entry["mode"] == ProcessingMode.LAZY else eager_fns).append(
            entry["func"]
        )

    # Filter to the date range + lookback buffer
    if total_lookback_days is not None:
        lookback_days = total_lookback_days
    else:
        lookback_days = _max_feature_lookback(features)

    lookback_calendar_days = calculate_calendar_lookback(lookback_days)
    buffer_start_date = start_date - datetime.timedelta(days=lookback_calendar_days)

    lf = (
        raw_records.with_columns(pl.col("date").dt.date().alias("date"))
        .filter(pl.col("date").is_between(buffer_start_date, end_date))
        .sort(["ticker", "date"])
    )

    # Apply lazy feature functions
    for feature_fn in lazy_fns:
        lf = feature_fn(lf)

    # Materialize once; streaming helps on large backfill
    out = lf.collect(engine="streaming")

    # Apply eager feature functions
    for feature_fn in eager_fns:
        out = feature_fn(out)

    model_state = out

    # For assets with sparse history, fill only close prices to avoid gaps
    # Forward-fill within ticker, then backfill earliest with first observed price
    if "adjusted_close_1d" in model_state.columns:
        model_state = model_state.with_columns(
            pl.col("adjusted_close_1d")
            .forward_fill()
            .over("ticker")
            .alias("adjusted_close_1d")
        ).with_columns(
            pl.col("adjusted_close_1d")
            .backward_fill()
            .over("ticker")
            .alias("adjusted_close_1d")
        )

    prices = construct_prices(model_state, universe)

    if not return_bundle:
        return model_state, prices

    if raw_data_bundle is None:
        logger.warning(
            "return_bundle=True was requested with legacy `lf` input; "
            "supplemental_model_state will be empty."
        )
        supplemental_model_state = _empty_supplemental_model_state(model_state=model_state)
    else:
        supplemental_model_state = _build_supplemental_model_state(
            raw_data_bundle.raw_supplemental_records
        )

    model_state_bundle = ModelStateBundle(
        model_state=model_state,
        supplemental_model_state=supplemental_model_state,
    )

    return model_state_bundle, prices


def _empty_supplemental_model_state(model_state: Optional[DataFrame] = None) -> DataFrame:
    if model_state is not None and "date" in model_state.columns:
        return model_state.select("date").unique().sort("date")
    return pl.DataFrame(schema={"date": pl.Date})


def _build_supplemental_model_state(raw_supplemental_records: LazyFrame) -> DataFrame:
    """
    Build supplemental state from non-ticker records:
      - normalize timestamp -> date
      - pivot series_id columns
    """
    try:
        sample = raw_supplemental_records.select("record_timestamp").limit(1).collect()
        if sample.is_empty():
            return _empty_supplemental_model_state()

        timestamp_dtype = sample.schema["record_timestamp"]
        base_type = timestamp_dtype.base_type() if hasattr(timestamp_dtype, "base_type") else timestamp_dtype

        if timestamp_dtype == pl.Utf8:
            date_expr = pl.col("record_timestamp").str.to_datetime(strict=False).dt.date()
        elif base_type == pl.Datetime:
            date_expr = pl.col("record_timestamp").dt.date()
        else:
            date_expr = pl.col("record_timestamp").cast(pl.Datetime, strict=False).dt.date()

        supplemental_df = (
            raw_supplemental_records
            .with_columns(date_expr.alias("date"))
            .drop("record_timestamp")
            .collect()
        )

        if supplemental_df.is_empty():
            return _empty_supplemental_model_state()

        return supplemental_df.pivot(
            index="date",
            on="series_id",
            values="value",
            aggregate_function="first",
        ).sort("date")
    except Exception as e:
        logger.warning(f"Could not load supplemental data: {e}. Creating empty supplemental_model_state.")
        return _empty_supplemental_model_state()


def _build_model_lazy_input(
        model_state: DataFrame, tickers: List[str], columns: List[str]
) -> pl.LazyFrame:
    cols = ["date", "ticker", *columns]
    return model_state.lazy().filter(pl.col("ticker").is_in(tickers)).select(cols)


def _infer_model_input_mode(runner) -> str:
    """
    Infer model runner input mode when registry metadata is absent.
    Defaults to legacy for safety.
    """
    try:
        params = list(inspect.signature(runner).parameters.values())
    except (TypeError, ValueError):
        return "legacy"

    if not params:
        return "legacy"

    first = params[0]
    annotation = first.annotation
    annotation_name = getattr(annotation, "__name__", str(annotation))

    if annotation is ModelStateBundle or "ModelStateBundle" in annotation_name:
        return "bundle"

    if first.name in {"bundle", "model_state_bundle"}:
        return "bundle"

    return "legacy"


def _ensure_lazy(obj: Union[pl.DataFrame, pl.LazyFrame]) -> pl.LazyFrame:
    if isinstance(obj, pl.LazyFrame):
        return obj
    if isinstance(obj, pl.DataFrame):
        return obj.lazy()
    raise TypeError(f"Model runner returned unsupported type: {type(obj)}")


def _coerce_weights_to_float(lf: pl.LazyFrame) -> pl.LazyFrame:
    cols = [c for c in lf.collect_schema().names() if c != "date"]
    if not cols:
        return lf
    return lf.with_columns([pl.col(cols).cast(pl.Float64)])


def _clamp_weights(lf: pl.LazyFrame, lo: float = -1.0, hi: float = 1.0) -> pl.LazyFrame:
    cols = [c for c in lf.collect_schema().names() if c != "date"]
    if not cols:
        return lf
    return lf.with_columns(pl.col(cols).clip(lo, hi))


def _pad_to_universe(lf: pl.LazyFrame, universe: List[str]) -> pl.LazyFrame:
    """
    Ensure lf has exactly ['date'] + universe columns:
      - add missing universe tickers as 0.0
      - drop any extra weight columns not in universe
      - order columns as ['date'] + universe
    """
    current = lf.collect_schema().names()
    weight_cols = [c for c in current if c != "date"]
    missing = [t for t in universe if t not in weight_cols]

    add_zero = [pl.lit(0.0).alias(t) for t in missing]
    lf2 = lf.with_columns(add_zero) if add_zero else lf

    return lf2.select(["date", *universe])


def _max_feature_lookback(features: Sequence[str]) -> int:
    return max((FEATURES[f]["lookback"] for f in features), default=0)


def calculate_max_lookback(
        features: Optional[List[str]] = None,
        models: Optional[List[str]] = None,
        aggregators: Optional[List[str]] = None,
        optimizers: Optional[List[str]] = None,
        feature_registry: dict = FEATURES,
        model_registry: dict = MODELS,
        aggregator_registry: dict = AGGREGATORS,
        optimizer_registry: dict = OPTIMIZERS,
) -> int:
    """
    Calculate the required lookback days for the pipeline.
    
    The lookback calculation works as follows:
    - Features need lookback: raw data BEFORE start_date for feature computation
    - Aggregators need lookback: model returns BEFORE start_date (which come from model_state)
    - Optimizers need lookback: price data BEFORE start_date (which come from model_state)
    
    Since aggregators and optimizers operate on model_state/prices (which depend on features),
    but they need historical data BEFORE start_date, we need:
    - Feature lookback: to compute features properly
    - Aggregator/Optimizer lookback: additional data BEFORE start_date for their warmup
    
    However, model_state is trimmed to start_date onwards, so aggregators/optimizers
    can't get historical data before start_date. The solution is to take the MAXIMUM:
    - If aggregator needs 240 days and features need 60 days, we need 240 days total
    - This ensures we have enough data BEFORE start_date for both features AND
      for aggregators/optimizers to have sufficient history after start_date
    
    Args:
        features: List of feature names (optional)
        models: List of model names (optional)
        aggregators: List of aggregator names (optional)
        optimizers: List of optimizer names (optional)
        feature_registry: Feature registry (defaults to FEATURES)
        model_registry: Model registry (defaults to MODELS)
        aggregator_registry: Aggregator registry (defaults to AGGREGATORS)
        optimizer_registry: Optimizer registry (defaults to OPTIMIZERS)
    
    Returns:
        Maximum lookback days across all used components
    """
    lookbacks = []

    # Features lookback: needed for feature computation
    if features:
        lookbacks.extend(
            feature_registry.get(f, {}).get("lookback", 0) for f in features
        )

    # Models lookback: models operate on features, so they don't need additional raw data lookback
    # (Their lookback is typically 0, but we include it for completeness)
    if models:
        lookbacks.extend(
            model_registry.get(m, {}).get("lookback", 0) for m in models
        )

    # Aggregators lookback: need historical model returns
    # Since model_state is trimmed to start_date onwards, aggregators need enough
    # lookback so that by the time they operate, they have sufficient history.
    # We take max to ensure we have enough data BEFORE start_date.
    if aggregators:
        lookbacks.extend(
            aggregator_registry.get(a, {}).get("lookback", 0) for a in aggregators
        )

    # Optimizers lookback: need historical price data
    # Similar to aggregators, we take max to ensure sufficient data BEFORE start_date.
    if optimizers:
        lookbacks.extend(
            optimizer_registry.get(o, {}).get("lookback", 0) for o in optimizers
        )

    return max(lookbacks, default=0)


def orchestrate_model_backtests(
        model_state: Optional[DataFrame] = None,
        models: Optional[List[str]] = None,
        universe: Optional[List[str]] = None,
        clamp_bounds: tuple[float, float] = (-1.0, 1.0),
        registry=MODELS,
        model_state_bundle: Optional[ModelStateBundle] = None,
) -> Dict[str, pl.LazyFrame]:
    """
    Run selected models and return per-model LazyFrames padded to the full universe:
      { model_name: LazyFrame(["date"] + universe) }, weights in [-1, 1].
    No aggregation or L1 normalization here.

    Compatibility modes:
      - legacy (default): runner receives filtered LazyFrame (old framework)
      - bundle: runner receives full ModelStateBundle (new framework)

    Model registry can opt into bundle mode with:
      "input_mode": "bundle"
    """
    if models is None or universe is None:
        raise ValueError("`models` and `universe` are required.")

    if model_state_bundle is None:
        if model_state is None:
            raise ValueError("Provide `model_state` or `model_state_bundle`.")
        model_state_bundle = ModelStateBundle(
            model_state=model_state,
            supplemental_model_state=_empty_supplemental_model_state(model_state=model_state),
        )

    if model_state is None:
        model_state = model_state_bundle.model_state

    lo, hi = clamp_bounds
    results: Dict[str, pl.LazyFrame] = {}

    for name in models:
        if name not in registry:
            raise KeyError(f"Unknown model: {name}")

        spec = registry[name]
        runner = spec["function"]
        input_mode = spec.get("input_mode", _infer_model_input_mode(runner))

        if input_mode == "bundle":
            out = _ensure_lazy(runner(model_state_bundle))
        elif input_mode == "legacy":
            tickers: List[str] = spec.get("tickers", [])
            columns: List[str] = spec.get("columns", [])
            if not tickers and not columns:
                # No legacy slice metadata usually means this is actually a bundle runner.
                if "input_mode" not in spec:
                    out = _ensure_lazy(runner(model_state_bundle))
                    out = _coerce_weights_to_float(out)  # float weights
                    out = _clamp_weights(out, lo, hi)  # clamp to bounds
                    out = _pad_to_universe(out, universe)  # add missing tickers as 0.0, reorder
                    results[name] = out
                    continue
                raise KeyError(
                    f"Model '{name}' is missing legacy input spec ('tickers'/'columns'). "
                    "Set input_mode='bundle' for bundle-based models."
                )
            lf_in = _build_model_lazy_input(model_state, tickers=tickers, columns=columns)
            out = _ensure_lazy(runner(lf_in))
        else:
            raise ValueError(
                f"Unsupported input_mode '{input_mode}' for model '{name}'. "
                "Expected 'legacy' or 'bundle'."
            )

        out = _coerce_weights_to_float(out)  # float weights
        out = _clamp_weights(out, lo, hi)  # clamp to bounds
        out = _pad_to_universe(out, universe)  # add missing tickers as 0.0, reorder

        results[name] = out

    return results


def construct_prices(model_state: DataFrame, universe: List[str]) -> pl.DataFrame:
    prices = (
        model_state.pivot(index="date", on="ticker", values="adjusted_close_1d")
        .sort("date")
        .with_columns(pl.col("date").cast(pl.String))
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
    )

    # Keep only ['date'] + universe (in that order). If some tickers aren't present, add them as null then fill.
    present = [c for c in prices.columns if c in universe]
    missing = [t for t in universe if t not in prices.columns]

    if missing:
        prices = prices.with_columns([pl.lit(None).alias(t) for t in missing])

    prices = (
        prices.select(["date", *universe])
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
    )
    return prices


def orchestrate_model_simulations(
        prices: DataFrame,
        model_insights: Dict[str, pl.LazyFrame],
        start_date: datetime.date,
        end_date: datetime.date,
        initial_value: float = 1_000_000.0,
        fee_model: str = "ibkr_pro_fixed",
        slippage_bps: float = 1.0,
) -> Dict[str, dict]:
    """
    Runs all backtests and returns { model_name: {"full_backtest_results": dict, "backtest_results": dict} }.
    No cross-model aggregation/weighting here.

    Runs TWO backtests per model:
    1. Full backtest (with lookback): data needed by aggregators for warmup
    2. Trimmed backtest [start_date, end_date]: canonical accurate results

    Returns:
        Dict[str, dict] where each model has:
        - "full_backtest_results": backtest dict with lookback data (for aggregators only)
        - "backtest_results": canonical backtest dict [start_date, end_date] (for storage/analysis)
    """
    backtester = HawkBacktester(
        initial_value, fee_model=fee_model, slippage_bps=slippage_bps
    )
    results: Dict[str, dict] = {}

    # Trim prices for accurate metrics
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    trimmed_prices = prices.filter(
        (pl.col("date") >= start_str) & (pl.col("date") <= end_str)
    )

    for name, lf in model_insights.items():
        weights = lf.collect()

        if weights.schema.get("date") != pl.String:
            weights = weights.with_columns(pl.col("date").cast(pl.String))

        price_cols = [c for c in prices.columns if c != "date"]
        if set(price_cols) != set(weights.columns) - {"date"}:
            # Add missing as 0.0, drop extras, enforce order
            missing = [c for c in price_cols if c not in weights.columns]
            if missing:
                weights = weights.with_columns([pl.lit(0.0).alias(c) for c in missing])
            weights = weights.select(["date", *price_cols]).fill_null(0.0)

        # Align to full price date range (fill missing dates with 0.0 weights)
        full_backtest_weights = (
            prices.select("date").join(weights, on="date", how="left").fill_null(0.0)
        )

        # Run FULL backtest for aggregator warmup
        full_backtest_results = backtester.run(prices, full_backtest_weights)

        # Align trimmed weights to trimmed price date range
        trimmed_backtest_weights = (
            trimmed_prices.select("date").join(weights, on="date", how="left").fill_null(0.0)
        )

        # Run TRIMMED backtest for accurate metrics
        backtest_results = backtester.run(trimmed_prices, trimmed_backtest_weights)

        # Store both results:
        # - full_backtest_results: for aggregators (includes lookback data)
        # - backtest_results: canonical results (starts fresh at start_date)
        results[name] = {
            "full_backtest_results": full_backtest_results,
            "backtest_results": backtest_results,
        }

    return results


def _enforce_l1_budget(lf: pl.LazyFrame, budget: float = 1.0) -> pl.LazyFrame:
    cols = [c for c in lf.collect_schema().names() if c != "date"]
    if not cols:
        return lf
    l1 = pl.sum_horizontal([pl.col(c).abs() for c in cols]).alias("_l1")
    lf2 = lf.with_columns(l1).with_columns(
        pl.when(pl.col("_l1") > budget)
        .then(budget / pl.col("_l1"))
        .otherwise(1.0)
        .alias("_scale")
    )
    return lf2.with_columns(
        [(pl.col(c) * pl.col("_scale")).alias(c) for c in cols]
    ).drop(["_l1", "_scale"])


def orchestrate_portfolio_aggregation(
        model_insights: Dict[str, pl.LazyFrame],
        backtest_results: Dict[str, dict],
        universe: List[str],
        aggregators: List[str],
        start_date: datetime.date,
        end_date: datetime.date,
        clamp_bounds: tuple[float, float] = (-1.0, 1.0),
        l1_budget: float = 1.0,
        registry=AGGREGATORS,
) -> Dict[str, DataFrame]:
    """
    Aggregate model insights into a single portfolio per aggregator.
    Returns { aggregator_name: wide DataFrame of weights }.

    Post-processing (padding, float coercion, clamping, L1 budget) happens here.
    If start_date/end_date are provided, skips the aggregator lookback warmup rows.
    """
    # Extract full_backtest_results from backtest_results for aggregators (they need full history)
    full_backtest_results = {
        model_name: results["full_backtest_results"]
        for model_name, results in backtest_results.items()
    }

    results: Dict[str, DataFrame] = {}
    for name in aggregators:
        if name not in registry:
            raise KeyError(f"Unknown aggregator: {name}")

        aggregator_fn = registry[name][
            "function"
        ]  # Callable[[Dict[str, LF], Dict], LF]
        aggregator_lookback = registry[name].get("lookback", 0)

        raw = aggregator_fn(model_insights, full_backtest_results)  # LazyFrame or DataFrame

        lf = _ensure_lazy(raw)
        lf = _coerce_weights_to_float(lf)
        lf = _pad_to_universe(lf, universe)
        lf = _clamp_weights(lf, *clamp_bounds)
        lf = _enforce_l1_budget(lf, budget=l1_budget)

        # Collect to get the dataframe
        df = lf.collect()

        # Skip warmup rows based on aggregator lookback
        # The aggregator produces weights for all dates, but the first `lookback` rows
        # are warmup period. We skip those to align output with start_date.
        if aggregator_lookback > 0 and len(df) > aggregator_lookback:
            # Skip the first `aggregator_lookback + 1` rows (off-by-one: we need lookback days BEFORE start)
            df = df.slice(aggregator_lookback + 1)

        results[name] = df

    return results


def orchestrate_portfolio_optimizations(
        prices: DataFrame,
        aggregated_insights: Dict[str, DataFrame],
        universe: List[str],
        optimizers: List[str],
        clamp_bounds: tuple[float, float] = (-1.0, 1.0),
        l1_budget: float = 1.0,
        registry=OPTIMIZERS,
) -> Dict[str, DataFrame]:
    """
    Run asset-level portfolio optimizers on aggregated desired weights.
    Assumes a single aggregated portfolio input for MVP; uses the first one if multiple.
    Returns { optimizer_name: wide DataFrame of weights }.
    """
    if not aggregated_insights:
        return {}

    # MVP: use the first aggregated set of weights
    agg_name, agg_df = next(iter(aggregated_insights.items()))

    results: Dict[str, DataFrame] = {}
    for name in optimizers:
        if name not in registry:
            raise KeyError(f"Unknown portfolio optimizer: {name}")

        optimizer_fn = registry[name][
            "function"
        ]  # Callable[[DataFrame, DataFrame, dict|None], LF]
        raw = optimizer_fn(prices, agg_df, None)  # LazyFrame or DataFrame

        lf = _ensure_lazy(raw)
        lf = _coerce_weights_to_float(lf)
        lf = _pad_to_universe(lf, universe)
        lf = _clamp_weights(lf, *clamp_bounds)
        lf = _enforce_l1_budget(lf, budget=l1_budget)

        results[name] = lf.collect()

    return results


def orchestrate_portfolio_simulations(
        prices: DataFrame,
        portfolio_insights: Dict[str, DataFrame],
        start_date: datetime.date,
        end_date: datetime.date,
        initial_value: float = 1_000_000.0,
        fee_model: str = "ibkr_pro_fixed",
        slippage_bps: float = 1.0,
):
    """
    Run backtests on portfolio weights.
    Filters prices to [start_date, end_date] to ensure backtest only runs on the desired date range.
    """
    # Trim prices to exact backtest date range
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    trimmed_prices = prices.filter(
        (pl.col("date") >= start_str) & (pl.col("date") <= end_str)
    )

    backtester = HawkBacktester(
        initial_value, fee_model=fee_model, slippage_bps=slippage_bps
    )
    results: Dict[str, dict] = {}

    for name, weights_df in portfolio_insights.items():
        weights = weights_df

        if weights.schema.get("date") != pl.String:
            weights = weights.with_columns(pl.col("date").cast(pl.String))

        price_cols = [c for c in trimmed_prices.columns if c != "date"]
        if set(price_cols) != set(weights.columns) - {"date"}:
            # Add missing as 0.0, drop extras, enforce order
            missing = [c for c in price_cols if c not in weights.columns]
            if missing:
                weights = weights.with_columns([pl.lit(0.0).alias(c) for c in missing])
            weights = weights.select(["date", *price_cols]).fill_null(0.0)

        # Align to trimmed price date range (fill missing dates with 0.0 weights)
        weights = (
            trimmed_prices.select("date").join(weights, on="date", how="left").fill_null(0.0)
        )

        results[name] = backtester.run(trimmed_prices, weights)

    return results


def run_full_backtest(
        universe: list[str],
        features: list[str],
        models: list[str],
        aggregators: list[str],
        optimizers: Optional[list[str]],
        start_date: datetime.date,
        end_date: datetime.date,
        initial_value: int = 1_000_000,
        fee_model: str = "ibkr_pro_fixed",
        slippage_bps: float = 1.0,
        model_registry: dict = MODELS,
        aggregator_registry: dict = AGGREGATORS,
        portfolio_optimizer_registry: dict = OPTIMIZERS,
):
    """Complete backtest orchestration (models → aggregation → optimization)."""

    # Calculate max lookback across all used components
    total_lookback_days = calculate_max_lookback(
        features=features,
        models=models,
        aggregators=aggregators,
        optimizers=optimizers,
        model_registry=model_registry,
        aggregator_registry=aggregator_registry,
        optimizer_registry=portfolio_optimizer_registry,
    )

    # Load data and create model state
    raw_data_bundle = read_data(include_supplemental=True)
    model_state_bundle, prices = create_model_state(
        raw_data_bundle=raw_data_bundle,
        features=features,
        start_date=start_date,
        end_date=end_date,
        universe=universe,
        total_lookback_days=total_lookback_days,
        return_bundle=True,
    )

    # Run models
    model_results = orchestrate_model_backtests(
        model_state_bundle=model_state_bundle,
        models=models,
        universe=universe,
        registry=model_registry,
    )

    # Run model simulations (uses full prices for aggregator warmup)
    model_simulations = orchestrate_model_simulations(
        prices=prices,
        model_insights=model_results,
        start_date=start_date,
        end_date=end_date,
        initial_value=initial_value,
        fee_model=fee_model,
        slippage_bps=slippage_bps,
    )

    # Run aggregation
    aggregated_results = orchestrate_portfolio_aggregation(
        model_insights=model_results,
        backtest_results=model_simulations,
        universe=universe,
        aggregators=aggregators,
        start_date=start_date,
        end_date=end_date,
        registry=aggregator_registry,
    )

    # Simulate aggregated portfolios
    aggregation_simulations = orchestrate_portfolio_simulations(
        prices=prices,
        portfolio_insights=aggregated_results,
        start_date=start_date,
        end_date=end_date,
        initial_value=initial_value,
        fee_model=fee_model,
        slippage_bps=slippage_bps,
    )

    # Optional: run asset-level optimization on top of aggregated weights
    optimizer_results: Dict[str, DataFrame] = {}
    optimizer_simulations: Dict[str, dict] = {}
    if optimizers:
        optimizer_results = orchestrate_portfolio_optimizations(
            prices=prices,
            aggregated_insights=aggregated_results,
            universe=universe,
            optimizers=optimizers,
            registry=portfolio_optimizer_registry,
        )
        optimizer_simulations = orchestrate_portfolio_simulations(
            prices=prices,
            portfolio_insights=optimizer_results,
            start_date=start_date,
            end_date=end_date,
            initial_value=initial_value,
            fee_model=fee_model,
            slippage_bps=slippage_bps,
        )

    return {
        "model_simulations": model_simulations,
        "aggregation_results": aggregated_results,
        "aggregation_simulations": aggregation_simulations,
        "optimizer_results": optimizer_results,
        "optimizer_simulations": optimizer_simulations,
    }
