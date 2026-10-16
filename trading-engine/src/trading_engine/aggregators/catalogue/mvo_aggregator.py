from typing import Callable, Dict, List, Optional, Literal, Tuple

from datetime import datetime
import numpy as np
import polars as pl
from polars import LazyFrame
import cvxpy as cp

from trading_engine.solvers import pick_solver


def _extract_returns_df(backtest_results: Dict[str, dict]) -> pl.DataFrame:
    """
    Build a wide DataFrame of model returns from backtest_results using the
    canonical 'daily_log_return' column in each model's backtest.

    :param backtest_results: { model_name: { 'backtest_results': pl.DataFrame | pd.DataFrame } }
    :return: pl.DataFrame with columns ['date', <model_names...>]
    """
    frames: List[pl.DataFrame] = []
    for model_name, payload in backtest_results.items():
        df = payload.get("backtest_results")
        if not isinstance(df, pl.DataFrame) or "daily_log_return" not in df.columns:
            continue
        if df.schema.get("date") != pl.String:
            df = df.with_columns(pl.col("date").cast(pl.String))
        frames.append(
            df.select(["date", "daily_log_return"]).rename(
                {"daily_log_return": model_name}
            )
        )

    if not frames:
        return pl.DataFrame({"date": []})

    out = frames[0]
    for nxt in frames[1:]:
        out = out.join(nxt, on="date", how="inner")
    return out.sort("date")


def _ensure_date_string(df: pl.DataFrame) -> pl.DataFrame:
    """
    Ensure the 'date' column is string-typed for consistent joins and pivots.

    :param df: Input DataFrame
    :return: DataFrame with 'date' cast to pl.String if needed
    """
    if df.schema.get("date") != pl.String:
        return df.with_columns(pl.col("date").cast(pl.String))
    return df


def _fallback_equal_weight(model_insights: Dict[str, LazyFrame]) -> LazyFrame:
    """
    Build an equal-weight combination across model weights when no returns exist.

    :param model_insights: Mapping of model name to LazyFrame of weights
    :return: LazyFrame of combined weights in wide format
    """
    if not model_insights:
        return pl.DataFrame({"date": []}).lazy()

    n: float = float(len(model_insights))
    longs: List[pl.LazyFrame] = [
        lf.unpivot(index="date", variable_name="ticker", value_name="w").with_columns(
            (pl.col("w") * (1.0 / n)).alias("w")
        )
        for lf in model_insights.values()
    ]
    combined_long = (
        pl.concat(longs, how="vertical")
        .group_by(["date", "ticker"])
        .agg(pl.col("w").sum().alias("w"))
    )
    wide = (
        combined_long.collect(engine="streaming")
        .pivot(values="w", index="date", columns="ticker", aggregate_function="first")
        .sort("date")
    )
    return wide.lazy()


def _prepare_returns(ret_wide: pl.DataFrame) -> Tuple[List[str], np.ndarray, List[str]]:
    """
    Extract model names, returns matrix, and date index from a wide returns table.

    :param ret_wide: Wide returns DataFrame with columns ['date', <models...>]
    :return: (model_names, returns_matrix (T,N), dates)
    """
    model_names: List[str] = [c for c in ret_wide.columns if c != "date"]
    dates: List[str] = ret_wide.select("date").to_series().to_list()
    ret_mat: np.ndarray = ret_wide.select(model_names).to_numpy()
    return model_names, ret_mat, dates


def _winsorize(
    window_returns: np.ndarray, lower_pct: float = 0.01, upper_pct: float = 0.99
) -> np.ndarray:
    """
    Winsorize returns column-wise at the given percentiles.

    :param window_returns: Array of shape (T, M)
    :param lower_pct: Lower percentile in [0, 1]
    :param upper_pct: Upper percentile in [0, 1]
    :return: Winsorized array (T, M)
    """
    if window_returns.size == 0:
        return window_returns
    lower = np.percentile(window_returns, 100.0 * lower_pct, axis=0)
    upper = np.percentile(window_returns, 100.0 * upper_pct, axis=0)
    return np.clip(window_returns, lower, upper)


def _ewma_weights(length: int, half_life: int) -> np.ndarray:
    """
    Create normalized EWMA weights of length T with specified half-life.

    :param length: Number of observations T
    :param half_life: Half-life HL (positive integer)
    :return: Weights w of shape (T,), sum(w)=1
    """
    if length <= 0:
        return np.array([], dtype=float)
    if half_life <= 0:
        # Fallback to equal weights
        w = np.ones(length, dtype=float)
        return w / w.sum()
    lam = 0.5 ** (1.0 / float(half_life))
    powers = np.arange(length - 1, -1, -1, dtype=float)
    raw = (1.0 - lam) * np.power(lam, powers)
    w = raw / raw.sum()
    return w


def _estimate_mu_eb_sr(
    window_returns: np.ndarray,
    hl_mean: int = 60,
    hl_vol: int = 60,
    winsor_pct: float = 0.01,
    vol_floor: float = 1e-6,
    scale: float = 1.0,
) -> np.ndarray:
    """
    Estimate expected returns μ via EWMA stats and Empirical-Bayes Sharpe shrinkage.

    Steps per the research memo:
      1) Winsorize returns (1%/99%).
      2) EWMA mean (HL_mean) and volatility (HL_vol).
      3) Decayed Sharpe per model.
      4) Estimate Sharpe uncertainty via effective sample size.
      5) Empirical-Bayes shrinkage of Sharpe.
      6) Map back to μ: μ = scale * SR_post * σ.

    :param window_returns: Array of shape (T, M) ordered oldest→newest
    :param hl_mean: EWMA half-life for mean
    :param hl_vol: EWMA half-life for volatility
    :param winsor_pct: Winsorization tail probability (e.g., 0.01 for 1%)
    :param vol_floor: Minimum volatility to avoid division by zero
    :param scale: Global scaling applied to μ
    :return: μ vector of shape (M,)
    """
    T, M = window_returns.shape if window_returns.ndim == 2 else (0, 0)
    if T == 0 or M == 0:
        return np.zeros((M,), dtype=float)

    # 1) Winsorize
    X = _winsorize(window_returns, lower_pct=winsor_pct,
                   upper_pct=1.0 - winsor_pct)

    # 2) EWMA mean and volatility
    w_mean = _ewma_weights(T, hl_mean)
    w_vol = _ewma_weights(T, hl_vol)

    mu_hat = w_mean @ X  # (M,)
    centered = X - mu_hat  # broadcast (T,M) - (M,) → (T,M)
    sigma_hat = np.sqrt(np.maximum((w_vol @ (centered**2)), vol_floor))

    # 3) Decayed Sharpe
    sr_hat = mu_hat / np.maximum(sigma_hat, vol_floor)

    # 4) Uncertainty via effective sample size
    teff = 1.0 / float(np.sum(np.square(w_mean)))
    v_i = (1.0 + 0.5 * (sr_hat**2)) / max(teff, 1.0)  # (M,)

    # 5) Empirical-Bayes shrinkage across models
    var_sr = float(np.var(sr_hat, ddof=1)) if M > 1 else 0.0
    tau2 = max(var_sr - float(np.mean(v_i)), 0.0)
    if tau2 <= 0.0:
        kappa = np.zeros_like(v_i)
    else:
        kappa = tau2 / (tau2 + v_i)
    sr_post = kappa * sr_hat

    # 6) Map back to μ
    mu = float(scale) * sr_post * sigma_hat
    return mu


def _solve_mvo(
    cov: np.ndarray,
    mu: np.ndarray,
    kappa: float,
    turnover_lambda: float,
    prev_w: Optional[np.ndarray],
    long_only: bool,
    w_min: Optional[np.ndarray] = None,
    w_max: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Solve the MVO problem with GMV=1 and optional long-only constraint using CVXPY.

    Objective: minimize 0.5 w^T (κ Σ) w - μ^T w + λ ||w - w_prev||_2^2
    Constraints:
      - If long_only: w >= 0, sum(w) == 1
      - Else:        ||w||_1 == 1 (implemented via w = w+ - w-, sum(w+)+sum(w-) == 1)
      - Optional box constraints: w_min <= w <= w_max

    :param cov: Covariance matrix (N x N)
    :param mu: Expected returns vector (N,)
    :param kappa: Risk aversion scalar κ applied to Σ
    :param turnover_lambda: Non-negative penalty on ||w - w_prev||^2
    :param prev_w: Previous weights or None
    :param long_only: Enforce non-negative weights and full investment if True
    :param w_min: Optional minimum weight bounds per model (N,)
    :param w_max: Optional maximum weight bounds per model (N,)
    :return: Weight vector (N,)
    """
    n: int = cov.shape[0]
    kappa_cov = kappa * cov
    # Symmetrize to reduce numerical asymmetry and wrap as PSD for CVXPY
    kappa_cov = 0.5 * (kappa_cov + kappa_cov.T)
    P_psd = cp.psd_wrap(kappa_cov)

    if long_only:
        w = cp.Variable(n, nonneg=True)
        constraints = [cp.sum(w) == 1.0]

        # Add box constraints if provided
        if w_min is not None:
            constraints.append(w >= w_min)
        if w_max is not None:
            constraints.append(w <= w_max)

        quad = 0.5 * cp.quad_form(w, P_psd)
        lin = -mu @ w
        reg = (
            turnover_lambda * cp.sum_squares(w - prev_w)
            if (turnover_lambda > 0.0 and prev_w is not None)
            else 0.0
        )
        objective = cp.Minimize(quad + lin + reg)
        problem = cp.Problem(objective, constraints)
        problem.solve(solver=pick_solver(), verbose=False)
        result = np.asarray(w.value, dtype=float).reshape(-1)
        # Numerical guard: ensure sum to 1 and non-negative
        result = np.maximum(result, 0.0)
        s = float(result.sum())
        return result / s if s > 0.0 else np.full(n, 1.0 / float(n))

    # L1 = 1 via positive/negative parts
    w_plus = cp.Variable(n, nonneg=True)
    w_minus = cp.Variable(n, nonneg=True)
    w = w_plus - w_minus
    constraints = [cp.sum(w_plus) + cp.sum(w_minus) == 1.0]

    # Add box constraints if provided
    if w_min is not None:
        constraints.append(w >= w_min)
    if w_max is not None:
        constraints.append(w <= w_max)

    quad = 0.5 * cp.quad_form(w, P_psd)
    lin = -mu @ w
    reg = (
        turnover_lambda * cp.sum_squares(w - prev_w)
        if (turnover_lambda > 0.0 and prev_w is not None)
        else 0.0
    )
    objective = cp.Minimize(quad + lin + reg)
    problem = cp.Problem(objective, constraints)
    problem.solve(solver=pick_solver(), verbose=False)
    result = np.asarray(w.value, dtype=float).reshape(-1)
    # Numerical guard: enforce L1 norm to 1
    l1 = float(np.sum(np.abs(result)))
    return result / l1 if l1 > 0.0 else np.full(n, 1.0 / float(n))


def _rolling_mvo_alphas(
    ret_wide: pl.DataFrame,
    risk_model: Callable[[np.ndarray], np.ndarray],
    cov_window_days: int,
    fallback: Literal["equal", "zero"],
    turnover_lambda: float,
    kappa: float,
    hl_mean: int = 60,
    hl_vol: int = 60,
    winsor_pct: float = 0.01,
    vol_floor: float = 1e-6,
    mu_scale: float = 1.0,
    long_only: bool = False,
    model_weight_bounds: Optional[Dict[str, Dict[str, float]]] = None,
    rebalance_interval: int = 1,
) -> pl.DataFrame:
    """
    Compute per-date model coefficients via rolling MVO.

    :param ret_wide: Wide model returns with ['date', <models...>]
    :param risk_model: Callable mapping window returns (T,N) -> covariance (N,N)
    :param cov_window_days: Rolling window length for covariance and μ estimation
    :param fallback: Warmup behavior when insufficient lookback ('equal' or 'zero')
    :param turnover_lambda: Non-negative penalty on turnover
    :param kappa: Risk aversion scaling on Σ
    :param model_weight_bounds: Optional dict mapping model_name -> {"min": float, "max": float}
    :param rebalance_interval: Frequency in trading days (e.g., 1=daily, 5=weekly, 20=monthly)
    :return: DataFrame of alphas with columns ['date', <models...>]
    """
    model_names, ret_mat, dates = _prepare_returns(ret_wide)
    if not model_names:
        return pl.DataFrame({"date": []})

    n_models = len(model_names)
    cov_win = int(cov_window_days)

    # Optional weight bounds
    w_min = w_max = None
    if model_weight_bounds is not None:
        w_min = np.array(
            [model_weight_bounds.get(m, {}).get("min", -np.inf)
             for m in model_names],
            dtype=float,
        )
        w_max = np.array(
            [model_weight_bounds.get(m, {}).get("max", np.inf)
             for m in model_names],
            dtype=float,
        )

    alphas, alpha_dates = [], []
    prev_w = None
    last_opt_w = None

    # Determine if "Tuesday rule" applies
    tuesday_mode = rebalance_interval >= 5
    week_interval = max(rebalance_interval // 5, 1)  # e.g. 1=weekly, 4=monthly
    last_rebalanced_week = None

    for i, date_str in enumerate(dates):
        if i + 1 < cov_win:
            # Warmup fallback
            if fallback == "zero":
                alphas.append([0.0] * n_models)
            else:
                alphas.append([1.0 / n_models] * n_models)
            alpha_dates.append(date_str)
            continue

        # --- Decide whether to rebalance ---
        rebalance_now = False
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")

        if not tuesday_mode:
            # Every N trading days
            if (i % rebalance_interval == 0) or (last_opt_w is None):
                rebalance_now = True
        else:
            # --- Tuesday-based weekly/monthly rule ---
            iso_year, iso_week, _ = date_obj.isocalendar()

            # Only consider rebalancing once per week_interval
            if last_rebalanced_week is None or (iso_week - last_rebalanced_week) >= week_interval:
                weekday = date_obj.weekday()  # Monday=0, Tuesday=1, ...
                if weekday == 1:
                    # Ideal case: it's Tuesday
                    rebalance_now = True
                    last_rebalanced_week = iso_week
                elif weekday > 1 and not any(
                    datetime.strptime(
                        d, "%Y-%m-%d").isocalendar()[1] == iso_week
                    and datetime.strptime(d, "%Y-%m-%d").weekday() == 1
                    for d in dates[:i]
                ):
                    # No Tuesday this week before today → rebalance today (holiday Tuesday)
                    rebalance_now = True
                    last_rebalanced_week = iso_week

        # --- Compute or carry forward weights ---
        if rebalance_now:
            window = ret_mat[i + 1 - cov_win: i + 1]
            cov = risk_model(window)
            mu = _estimate_mu_eb_sr(
                window, hl_mean, hl_vol, winsor_pct, vol_floor, mu_scale
            )
            w_new = _solve_mvo(
                cov=cov,
                mu=mu,
                kappa=kappa,
                turnover_lambda=turnover_lambda,
                prev_w=prev_w,
                long_only=long_only,
                w_min=w_min,
                w_max=w_max,
            )
            last_opt_w = w_new
            w = w_new
        else:
            w = prev_w if prev_w is not None else np.full(
                n_models, 1.0 / n_models)

        alphas.append(w.tolist())
        alpha_dates.append(date_str)
        prev_w = w

    alpha_df = pl.DataFrame(
        {"date": alpha_dates, **{m: [row[j] for row in alphas]
                                 for j, m in enumerate(model_names)}}
    )
    return alpha_df


def _scale_and_combine_weights(
    model_insights: Dict[str, LazyFrame],
    model_names: List[str],
    alpha_df: pl.DataFrame,
) -> LazyFrame:
    """
    Combine per-model ticker weights scaled by alpha(date, model).

    :param model_insights: Mapping from model name -> LazyFrame of weights
    :param model_names: Ordered list of model names
    :param alpha_df: DataFrame with ['date', <models...>] alphas
    :return: LazyFrame of combined weights in wide form
    """
    if not model_names:
        return pl.DataFrame({"date": []}).lazy()

    scaled_longs: List[pl.LazyFrame] = []
    for m in model_names:
        lf = model_insights.get(m)
        if lf is None:
            continue
        long_w = lf.melt(
            id_vars="date", variable_name="ticker", value_name="w"
        ).with_columns(pl.col("date").cast(pl.String))

        a = alpha_df.select(["date", m]).rename({m: "alpha"}).lazy()
        scaled = (
            long_w.join(a, on="date", how="inner")
            .with_columns((pl.col("w") * pl.col("alpha")).alias("w"))
            .select(["date", "ticker", "w"])
        )
        scaled_longs.append(scaled)

    if not scaled_longs:
        return pl.DataFrame({"date": []}).lazy()

    combined_long = (
        pl.concat(scaled_longs, how="vertical")
        .group_by(["date", "ticker"])
        .agg(pl.col("w").sum().alias("w"))
    )
    combined_wide = (
        combined_long.collect(engine="streaming")
        .pivot(values="w", index="date", columns="ticker", aggregate_function="first")
        .sort("date")
    )
    return combined_wide.lazy()


def MVOAggregator(
    cov_window_days: int = 60,
    # type: ignore[assignment]
    risk_model: Callable[[np.ndarray], np.ndarray] = None,
    fallback: Literal["equal", "zero"] = "equal",
    turnover_lambda: float = 0.0,
    kappa: float = 1.0,
    hl_mean: int = 60,
    hl_vol: int = 60,
    winsor_pct: float = 0.01,
    vol_floor: float = 1e-6,
    mu_scale: float = 1.0,
    long_only: bool = False,
    model_weight_bounds: Optional[Dict[str, Dict[str, float]]] = None,
    rebalance_interval: int = 1,
) -> Callable[[Dict[str, LazyFrame], Dict], LazyFrame]:
    """
    Aggregator that computes model-level mean-variance weights using cross-model
    return covariances, then combines per-model ticker weights accordingly.

    :param cov_window_days: Rolling window length (days) for model return covariance.
    :param risk_model: Callable(window_returns)->covariance; must be provided by the caller.
    :param fallback: warmup behavior when insufficient lookback ("equal" or "zero").
    :param turnover_lambda: non-negative penalty weight on turnover ||w - w_prev||^2 (0 disables).
    :param kappa: risk aversion scaling on Σ (κ Σ); κ>1 → closer to min-var, κ<1 → more return-seeking
    :param hl_mean: Half-life for EWMA mean used in μ estimation
    :param hl_vol: Half-life for EWMA volatility used in μ estimation
    :param winsor_pct: Winsorization tail probability (e.g., 0.01 → 1% tails)
    :param vol_floor: Minimum volatility floor to avoid division by zero
    :param mu_scale: Global scaling factor applied to μ
    :param long_only: If True, enforce non-negative weights before GMV normalization
    :param model_weight_bounds: Optional dict mapping model_name -> {"min": float, "max": float}
                                to constrain individual model weights (e.g., {"Model1": {"min": 0.0, "max": 0.3}})
    :param rebalance_interval: Rebalance frequency in days
    :return: Callable mapping (model_insights, backtest_results) -> LazyFrame of combined weights.
    """

    def run(model_insights: Dict[str, LazyFrame], backtest_results: Dict) -> LazyFrame:
        if not model_insights:
            return pl.DataFrame({"date": []}).lazy()

        # 1) Build wide returns table per model
        ret_wide = _extract_returns_df(backtest_results)
        if ret_wide.is_empty():
            return _fallback_equal_weight(model_insights)

        # Ensure date string
        ret_wide = _ensure_date_string(ret_wide)

        model_names, _, _ = _prepare_returns(ret_wide)
        if not model_names:
            return pl.DataFrame({"date": []}).lazy()

        # 2) Compute per-date model coefficients via rolling MVO
        if risk_model is None:
            raise ValueError(
                "risk_model must be provided: a callable(window_returns)->covariance"
            )
        alpha_df = _rolling_mvo_alphas(
            ret_wide=ret_wide,
            risk_model=risk_model,
            cov_window_days=cov_window_days,
            fallback=fallback,
            turnover_lambda=turnover_lambda,
            kappa=kappa,
            hl_mean=hl_mean,
            hl_vol=hl_vol,
            winsor_pct=winsor_pct,
            vol_floor=vol_floor,
            mu_scale=mu_scale,
            long_only=long_only,
            model_weight_bounds=model_weight_bounds,
            rebalance_interval=rebalance_interval,
        )

        # 3) Combine per-model ticker weights scaled by alphas(date, model)
        combined = _scale_and_combine_weights(
            model_insights=model_insights, model_names=model_names, alpha_df=alpha_df
        )
        return combined

    return run
