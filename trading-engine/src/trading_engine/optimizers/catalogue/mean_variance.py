from typing import Callable, Optional, Protocol, List, Tuple, Dict

import cvxpy as cp
import numpy as np
import polars as pl
from polars import DataFrame, LazyFrame

from trading_engine.solvers import pick_solver


class RiskModel(Protocol):
    def __call__(self, window_returns: np.ndarray) -> np.ndarray: ...


def _compute_log_returns(prices: DataFrame) -> DataFrame:
    """
    Compute per-ticker daily log returns from a wide price table.

    :param prices: Polars DataFrame with first column 'date' and remaining columns tickers
    :return: Polars DataFrame of log returns aligned to dates (first row per ticker becomes null then filled with 0)
    """
    tickers = [c for c in prices.columns if c != "date"]
    if not tickers:
        return pl.DataFrame({"date": []})

    lr = (
        prices.with_columns(
            [
                (pl.col(t).cast(pl.Float64) / pl.col(t).shift(1).cast(pl.Float64))
                .log()
                .alias(f"ret_{t}")
                for t in tickers
            ]
        )
        .select(["date", *[f"ret_{t}" for t in tickers]])
        .with_columns([pl.col(f"ret_{t}").fill_null(0.0) for t in tickers])
    )
    return lr


def _ensure_date_string(df: DataFrame) -> DataFrame:
    """
    Ensure the 'date' column is string-typed for reliable joins.

    :param df: Input DataFrame
    :return: DataFrame with 'date' as pl.String
    """
    if df.schema.get("date") != pl.String:
        return df.with_columns(pl.col("date").cast(pl.String))
    return df


def _align_prices_to_tickers(prices_df: DataFrame, tickers: List[str]) -> DataFrame:
    """
    Ensure prices table includes all tickers, forward/backward filling nulls.

    :param prices_df: Input wide prices DataFrame
    :param tickers: Target tickers to include
    :return: Prices DataFrame aligned to tickers
    """
    missing = [t for t in tickers if t not in prices_df.columns]
    if missing:
        prices_df = prices_df.with_columns([pl.lit(None).alias(t) for t in missing])
    prices_df = (
        prices_df.select(["date", *tickers])
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
    )
    return prices_df


def _prepare_joined(
        prices_df: DataFrame, desired_weights_df: DataFrame
) -> Tuple[List[str], DataFrame]:
    """
    Align prices to desired weights tickers, compute returns, and inner-join by date.

    :param prices_df: Wide prices table with columns ['date', <tickers...>]
    :param desired_weights_df: Desired weights wide table with ['date', <tickers...>]
    :return: (tickers, joined_df) where joined has returns and target weights
    """
    tickers = [c for c in desired_weights_df.columns if c != "date"]
    if not tickers:
        return [], pl.DataFrame({"date": []})

    prices_df = _align_prices_to_tickers(prices_df, tickers)

    returns_df = _compute_log_returns(prices_df)
    returns_df = _ensure_date_string(returns_df)
    dw = _ensure_date_string(desired_weights_df)

    joined = returns_df.join(dw, on="date", how="inner")
    return tickers, joined


def _build_matrices(
        joined: DataFrame, tickers: List[str]
) -> Tuple[List[str], np.ndarray, np.ndarray, List[str]]:
    """
    Build numpy matrices needed for rolling optimization.

    :param joined: Joined DataFrame of returns and target weights
    :param tickers: List of asset tickers
    :return: (ret_cols, ret_mat, target_mat, dates)
    """
    ret_cols: List[str] = [c for c in joined.columns if c.startswith("ret_")]
    dates: List[str] = joined.select("date").to_series().to_list()
    ret_mat: np.ndarray = joined.select(ret_cols).to_numpy()
    target_mat: np.ndarray = joined.select(tickers).to_numpy()
    return ret_cols, ret_mat, target_mat, dates


def _solve_mv(
        cov: np.ndarray,
        mu: np.ndarray,
        target_w: np.ndarray,
        gamma: float,
        lambda_te: float,
        kappa: float,
        w_prev: Optional[np.ndarray] = None,
        turnover_lambda: float = 0.0,
        w_min: Optional[np.ndarray] = None,
        w_max: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Solve the mean-variance tracking-error objective with optional turnover penalty:

    minimize_w  0.5 w^T (κ Σ) w  -  γ μ^T w  +  λ_te ||w - w_target||^2  +  λ_turnover ||w - w_prev||^2

    With optional box constraints: w_min <= w <= w_max

    :param cov: Covariance matrix Σ (N x N)
    :param mu: Expected returns vector μ (N,)
    :param target_w: Tracking target weights (N,)
    :param gamma: Return-seeking weight γ
    :param lambda_te: Tracking-error weight λ (L2) against target weights
    :param kappa: Risk scaling κ applied to Σ
    :param w_prev: Previous weights (N,) for turnover penalty
    :param turnover_lambda: Turnover penalty weight (0 disables)
    :param w_min: Optional minimum weight bounds per asset (N,)
    :param w_max: Optional maximum weight bounds per asset (N,)
    :return: Solution vector w (N,)
    """
    n = cov.shape[0]

    # Check if we have any finite constraints or turnover penalty
    has_constraints = False
    if w_min is not None and np.any(np.isfinite(w_min)):
        has_constraints = True
    if w_max is not None and np.any(np.isfinite(w_max)):
        has_constraints = True

    # If no constraints and no turnover penalty, use closed-form solution (faster)
    if not has_constraints and (turnover_lambda <= 0.0 or w_prev is None):
        A = (kappa * cov) + (2.0 * lambda_te) * np.eye(n)
        b = gamma * mu + 2.0 * lambda_te * target_w
        try:
            return np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            return np.linalg.pinv(A) @ b

    # If no box constraints but has turnover, still use closed-form
    if not has_constraints and turnover_lambda > 0.0 and w_prev is not None:
        A = (kappa * cov) + 2.0 * (lambda_te + turnover_lambda) * np.eye(n)
        b = gamma * mu + 2.0 * lambda_te * target_w + 2.0 * turnover_lambda * w_prev
        try:
            return np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            return np.linalg.pinv(A) @ b

    # Use CVXPY for constrained optimization
    w = cp.Variable(n)
    kappa_cov = kappa * cov
    # Symmetrize for numerical stability
    kappa_cov = 0.5 * (kappa_cov + kappa_cov.T)
    P_psd = cp.psd_wrap(kappa_cov)

    # Clean inputs - replace NaN/inf with safe values
    mu_clean = np.nan_to_num(mu, nan=0.0, posinf=0.0, neginf=0.0)
    target_w_clean = np.nan_to_num(target_w, nan=0.0, posinf=0.0, neginf=0.0)

    # Objective: 0.5 w^T (κ Σ) w  -  γ μ^T w  +  λ_te ||w - w_target||^2  +  λ_turnover ||w - w_prev||^2
    quad_risk = 0.5 * cp.quad_form(w, P_psd)
    lin_return = -gamma * (mu_clean @ w)
    tracking_error = lambda_te * cp.sum_squares(w - target_w_clean)

    # Add turnover penalty if enabled
    turnover_penalty = 0.0
    if turnover_lambda > 0.0 and w_prev is not None:
        w_prev_clean = np.nan_to_num(w_prev, nan=0.0, posinf=0.0, neginf=0.0)
        turnover_penalty = turnover_lambda * cp.sum_squares(w - w_prev_clean)

    objective = cp.Minimize(quad_risk + lin_return + tracking_error + turnover_penalty)

    constraints = []
    # Only add constraints for finite bounds (MOSEK doesn't handle inf well)
    if w_min is not None:
        finite_min = np.isfinite(w_min)
        if np.any(finite_min):
            for i in range(n):
                if finite_min[i]:
                    constraints.append(w[i] >= w_min[i])
    if w_max is not None:
        finite_max = np.isfinite(w_max)
        if np.any(finite_max):
            for i in range(n):
                if finite_max[i]:
                    constraints.append(w[i] <= w_max[i])

    problem = cp.Problem(objective, constraints)
    problem.solve(solver=pick_solver(), verbose=False)

    if w.value is None:
        # Fallback to unconstrained if solver fails
        A = (kappa * cov) + (2.0 * lambda_te) * np.eye(n)
        b = gamma * mu + 2.0 * lambda_te * target_w
        try:
            return np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            return np.linalg.pinv(A) @ b

    return np.asarray(w.value, dtype=float).reshape(-1)


def _apply_position_delta_constraint(
        w_new: np.ndarray, w_prev: np.ndarray, min_delta: float
) -> np.ndarray:
    """
    Apply minimum position change constraint: if changing a position,
    must change by at least min_delta, otherwise keep it unchanged.

    :param w_new: Proposed new weights (N,)
    :param w_prev: Previous weights (N,)
    :param min_delta: Minimum absolute position change required
    :return: Adjusted weights (N,)
    """
    delta = np.abs(w_new - w_prev)
    # Where delta is too small (but non-zero), revert to previous weight
    too_small = (delta > 0) & (delta < min_delta)
    w_adjusted = w_new.copy()
    w_adjusted[too_small] = w_prev[too_small]
    return w_adjusted


def _rolling_optimize(
        dates: List[str],
        ret_mat: np.ndarray,
        target_mat: np.ndarray,
        cov_window_days: int,
        risk_model: RiskModel,
        gamma: float,
        lambda_te: float,
        kappa: float,
        w_min: Optional[np.ndarray] = None,
        w_max: Optional[np.ndarray] = None,
        min_position_delta: float = 0.0,
        turnover_lambda: float = 0.0,
) -> Tuple[List[str], List[List[float]]]:
    """
    Compute rolling mean-variance optimized weights over dates.

    :param dates: Date index
    :param ret_mat: Matrix of asset returns (T, N)
    :param target_mat: Desired weights per date (T, N)
    :param cov_window_days: Rolling window for covariance estimation
    :param risk_model: Callable mapping window returns -> covariance
    :param gamma: Return-seeking weight
    :param lambda_te: Tracking-error weight
    :param kappa: Risk scaling
    :param w_min: Optional minimum weight bounds per asset (N,)
    :param w_max: Optional maximum weight bounds per asset (N,)
    :param min_position_delta: Minimum position change required (0 disables)
    :param turnover_lambda: Turnover penalty weight (0 disables)
    :return: (out_dates, out_weights)
    """
    out_weights: List[List[float]] = []
    out_dates: List[str] = []

    for i in range(len(dates)):
        if i + 1 < cov_window_days:
            # Not enough history: use target weights passthrough
            out_weights.append(target_mat[i].tolist())
            out_dates.append(dates[i])
            continue

        window = ret_mat[i + 1 - cov_window_days: i + 1]
        cov = risk_model(window)
        mu = target_mat[i]
        target_w = target_mat[i]

        # Get previous weights for turnover penalty
        w_prev = np.array(out_weights[-1], dtype=float) if out_weights else None

        w = _solve_mv(
            cov=cov,
            mu=mu,
            target_w=target_w,
            gamma=gamma,
            lambda_te=lambda_te,
            kappa=kappa,
            w_prev=w_prev,
            turnover_lambda=turnover_lambda,
            w_min=w_min,
            w_max=w_max,
        )

        # Apply minimum position delta constraint if enabled
        if min_position_delta > 0.0 and w_prev is not None:
            w = _apply_position_delta_constraint(w, w_prev, min_position_delta)

        out_weights.append(w.tolist())
        out_dates.append(dates[i])

    return out_dates, out_weights


def MeanVarianceOptimizer(
        cov_window_days: int = 60,
        gamma: float = 1.0,
        lambda_te: float = 1.0,
        risk_model: Optional[RiskModel] = None,
        kappa: float = 1.0,
        asset_weight_bounds: Optional[Dict[str, Dict[str, float]]] = None,
        min_position_delta: float = 0.0,
        turnover_lambda: float = 0.0,
) -> Callable[[DataFrame, DataFrame, dict | None], LazyFrame]:
    """
    Mean-variance optimizer with tracking error penalty, turnover penalty, and optional constraints.

    Objective per date t:
      minimize_w  0.5 w^T Σ w  -  γ μ^T w  +  λ_te ||w - w_target||^2  +  λ_turnover ||w - w_prev||^2

    Closed form solution (unconstrained):
      (Σ + 2(λ_te + λ_turnover) I) w = γ μ + 2λ_te w_target + 2λ_turnover w_prev

    :param cov_window_days: Rolling window length (days) for covariance Σ_assets estimation.
    :param gamma: Weight on the expected-return term μ_assets^T w (higher → more return-seeking).
    :param lambda_te: L2 tracking penalty weight to stay close to desired weights w_target.
    :param risk_model: Callable(window_returns)->covariance; must be provided by the caller.
    :param kappa: Risk aversion scaling on Σ_assets (κ Σ). κ=1.0 preserves current behavior.
    :param asset_weight_bounds: Optional dict mapping ticker -> {"min": float, "max": float}
                                to constrain individual asset weights (e.g., {"SPY-US": {"min": 0.0, "max": 0.5}})
    :param min_position_delta: Minimum position change required to rebalance (0 disables).
                               E.g., 0.03 means only rebalance if changing by at least 3%.
    :param turnover_lambda: L2 turnover penalty weight to minimize changes from previous weights (0 disables).
                            Helps reduce transaction costs.
    :return: Callable mapping (prices_df, desired_weights_df, config) → LazyFrame wide weights.
    """

    def run(
            prices_df: DataFrame, desired_weights_df: DataFrame, config: dict | None = None
    ) -> LazyFrame:
        if prices_df.is_empty() or desired_weights_df.is_empty():
            return pl.DataFrame({"date": []}).lazy()

        if risk_model is None:
            raise ValueError(
                "risk_model must be provided: a callable(window_returns)->covariance"
            )

        tickers, joined = _prepare_joined(
            prices_df=prices_df, desired_weights_df=desired_weights_df
        )
        if not tickers or joined.is_empty():
            return pl.DataFrame({"date": []}).lazy()

        # Convert asset weight bounds dictionary to arrays
        w_min: Optional[np.ndarray] = None
        w_max: Optional[np.ndarray] = None
        if asset_weight_bounds is not None:
            w_min = np.array(
                [asset_weight_bounds.get(t, {}).get("min", -np.inf) for t in tickers],
                dtype=float,
            )
            w_max = np.array(
                [asset_weight_bounds.get(t, {}).get("max", np.inf) for t in tickers],
                dtype=float,
            )

        ret_cols, ret_mat, target_mat, dates = _build_matrices(joined, tickers)
        out_dates, out_weights = _rolling_optimize(
            dates=dates,
            ret_mat=ret_mat,
            target_mat=target_mat,
            cov_window_days=cov_window_days,
            risk_model=risk_model,
            gamma=gamma,
            lambda_te=lambda_te,
            kappa=kappa,
            w_min=w_min,
            w_max=w_max,
            min_position_delta=min_position_delta,
            turnover_lambda=turnover_lambda,
        )

        result_df = pl.DataFrame(
            {
                "date": out_dates,
                **{t: [row[j] for row in out_weights] for j, t in enumerate(tickers)},
            }
        )
        return result_df.lazy()

    return run
