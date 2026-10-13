"""
generate_targets.py — Build monthly target weights for the paper portfolio.

Supports multiple optimizers (selected via the ``optimizer`` arg or the
``CAPITALFUND_OPTIMIZER`` env var). All optimizers operate on the model
universe, the ``etfs.core`` list in config/universes/etfs.yaml, and nothing
else: the master price file also keeps series the model no longer holds
(TQQQ, out since 2026-10-12), and those never get a target.

  * ``equal_weight``       — 1/N across every eligible ETF
  * ``inverse_vol``        — weight ∝ 1 / σ_i  (diversified, robust default)
  * ``risk_parity``        — Newton-step approximation of equal risk contribution
  * ``momentum_weighted``  — weight ∝ max(momentum_i, 0); only positive-momentum
                             ETFs are held, weights normalized to sum to 1
  * ``momentum_tilted``    — equal-weight × (1 + momentum_tilt × z-scored mom),
                             a soft tilt toward winners while still holding all
  * ``momentum_top5``      — legacy behavior: top-5 by momentum, equal-weight

The default is ``inverse_vol`` because it diversifies across the full universe,
gives lower-volatility assets (Treasuries, T-bills) more weight than equity
ETFs, and produces the smallest drawdowns historically.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

# Look-back windows used by the optimizers (in trading days).
VOLATILITY_LOOKBACK = 63        # ~3 months of daily vol
MOMENTUM_LOOKBACK_DEFAULT = 126  # ~6 months
MIN_HISTORY_DEFAULT = 126        # ~6 months before an ETF can be held
# How far behind a symbol's last bar may be and still count at a rebalance
# (business days, as the price validator's staleness warning counts them).
MAX_LAG_SESSIONS = 3

OPTIMIZERS_AVAILABLE = (
    "equal_weight",
    "inverse_vol",
    "risk_parity",
    "momentum_weighted",
    "momentum_tilted",
    "momentum_top5",
    # Signal-ensemble-driven (uses src/strategies/etf):
    "ensemble_default",
    "ensemble_momentum_heavy",
    "ensemble_reversion_heavy",
)
DEFAULT_OPTIMIZER = "ensemble_default"


@dataclass
class TargetResult:
    ok: bool
    profile: str
    action: str
    rows_written: int
    rebalance_dates: int
    symbols_considered: int
    optimizer: str
    latest_rebalance_date: str | None
    csv_path: str
    parquet_path: str
    preview: list[dict[str, Any]]
    universe: list[str] = field(default_factory=list)
    # in the model universe but with no rows in the master: never eligible
    universe_missing: list[str] = field(default_factory=list)
    # the last session read; master rows after it (a session not yet over) are not
    prices_through: str | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


UNIVERSE_PATH = _project_root() / "config" / "universes" / "etfs.yaml"


def model_universe(path: Path | None = None) -> tuple[str, ...]:
    """The ETFs the model may hold: ``etfs.core`` in config/universes/etfs.yaml.

    A missing file or an empty list is an error rather than "every symbol in
    the master", which is what target generation used to trade.
    """
    import yaml

    path = path or UNIVERSE_PATH
    with open(path, encoding="utf-8") as f:
        core = ((yaml.safe_load(f) or {}).get("etfs") or {}).get("core") or []
    symbols = tuple(dict.fromkeys(str(s).strip().upper() for s in core if str(s).strip()))
    if not symbols:
        raise ValueError(f"No model universe: {path} has no etfs.core list")
    return symbols


def _paths() -> dict[str, Path]:
    root = _project_root()
    return {
        "prices_csv": root / "data" / "market" / "cleaned" / "prices" / "etf_prices_master.csv",
        "prices_parquet": root / "data" / "market" / "cleaned" / "prices" / "etf_prices_master.parquet",
        "targets_csv": root / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.csv",
        "targets_parquet": root / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet",
    }


def _load_prices(universe: Iterable[str] | None = None) -> pd.DataFrame:
    """The master's closes, limited to ``universe`` when given."""
    p = _paths()
    if p["prices_parquet"].exists():
        df = pd.read_parquet(p["prices_parquet"])
    elif p["prices_csv"].exists():
        df = pd.read_csv(p["prices_csv"])
    else:
        raise FileNotFoundError("Master price file not found.")

    df.columns = [c.lower().strip() for c in df.columns]
    required = ["date", "symbol", "close"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column in master prices: {col}")

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    df = df.dropna(subset=["date", "symbol", "close"])
    if universe is not None:
        df = df[df["symbol"].isin(set(universe))]
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    return df


def _build_features(
    df: pd.DataFrame,
    momentum_lookback: int,
    min_history: int,
) -> pd.DataFrame:
    df = df.copy()

    df["obs_count"] = df.groupby("symbol").cumcount() + 1
    df["mom"] = df.groupby("symbol")["close"].pct_change(momentum_lookback)
    df["log_ret"] = (
        df.groupby("symbol")["close"].apply(lambda s: np.log(s).diff()).reset_index(level=0, drop=True)
    )
    # Trailing volatility (annualized, in % terms).
    df["vol"] = (
        df.groupby("symbol")["log_ret"]
        .rolling(VOLATILITY_LOOKBACK, min_periods=VOLATILITY_LOOKBACK // 2)
        .std()
        .reset_index(level=0, drop=True)
        * np.sqrt(252)
    )
    df["eligible"] = (df["obs_count"] >= min_history) & df["mom"].notna() & df["vol"].notna()

    df["month"] = df["date"].dt.to_period("M")

    return df


# ── Optimizers ──────────────────────────────────────────────────────────────

def _equal_weight(snap: pd.DataFrame) -> np.ndarray:
    n = len(snap)
    return np.full(n, 1.0 / n) if n > 0 else np.array([])


def _inverse_vol(snap: pd.DataFrame) -> np.ndarray:
    vol = snap["vol"].astype(float).to_numpy()
    inv = np.where(vol > 0, 1.0 / vol, 0.0)
    total = inv.sum()
    return (inv / total) if total > 0 else _equal_weight(snap)


def _risk_parity(snap: pd.DataFrame, returns_history: pd.DataFrame) -> np.ndarray:
    """
    Simple risk-parity: equal risk contribution under the assumption of zero
    correlation. Reduces to inverse-vol weighting when correlations are
    ignored, which is a reasonable approximation for diversified ETF
    universes.
    """
    return _inverse_vol(snap)


def _momentum_weighted(snap: pd.DataFrame) -> np.ndarray:
    mom = snap["mom"].astype(float).to_numpy()
    # Only hold ETFs with positive momentum.
    weights = np.where(mom > 0, mom, 0.0)
    total = weights.sum()
    if total <= 0:
        # No positive-momentum ETFs — fall back to inverse-vol so we stay invested.
        return _inverse_vol(snap)
    return weights / total


def _momentum_tilted(snap: pd.DataFrame, tilt: float = 0.5) -> np.ndarray:
    """
    Equal-weight base × (1 + tilt × z-scored momentum). Tilt = 0 → equal
    weight. Tilt = 1 → strong concentration in winners. Tilt = 0.5 (default)
    gives a moderate momentum tilt while still holding every ETF.
    """
    n = len(snap)
    if n == 0:
        return np.array([])
    mom = snap["mom"].astype(float).to_numpy()
    if np.isnan(mom).all() or np.std(mom) == 0:
        return _equal_weight(snap)
    z = (mom - np.nanmean(mom)) / (np.nanstd(mom) + 1e-9)
    multipliers = np.maximum(1.0 + tilt * z, 0.0)
    if multipliers.sum() <= 0:
        return _equal_weight(snap)
    raw = multipliers / multipliers.sum()
    # Floor: never let any single ETF go to zero in this mode — minimum 1%.
    floor = 0.01
    raw = np.maximum(raw, floor)
    return raw / raw.sum()


def _momentum_top5(snap: pd.DataFrame, top_k: int = 5) -> np.ndarray:
    """Legacy behaviour: equal-weight top-K by momentum, zero everywhere else."""
    n = len(snap)
    weights = np.zeros(n)
    if n == 0:
        return weights
    mom = snap["mom"].astype(float).to_numpy()
    order = np.argsort(-mom)  # descending
    pick = order[:top_k]
    weights[pick] = 1.0 / len(pick)
    return weights


def _ensemble_score_to_weights(
    snap: pd.DataFrame,
    score_by_symbol: dict[str, float],
    *,
    floor: float = 0.02,
    softmax_temp: float = 3.0,
) -> np.ndarray:
    """
    Convert a per-symbol composite score into a long-only weight vector.

    Procedure:
      1. Look up the score for each symbol in ``snap`` (default 0 if missing).
      2. Apply a softmax with temperature to keep weights smooth — higher
         scores get more weight but losers still get something.
      3. Floor every weight at ``floor`` so no asset is dropped entirely
         (matches user requirement: hold all eligible ETFs).
      4. Renormalize to sum to 1.
    """
    symbols = snap["symbol"].astype(str).str.upper().to_numpy()
    raw = np.array([float(score_by_symbol.get(s, 0.0)) for s in symbols])
    if len(raw) == 0:
        return np.array([])

    # Temperature-scaled softmax (numerical-stable).
    z = raw / max(softmax_temp, 1e-6)
    z = z - z.max()
    weights = np.exp(z)
    if weights.sum() <= 0:
        return _equal_weight(snap)
    weights = weights / weights.sum()
    weights = np.maximum(weights, floor)
    weights = weights / weights.sum()
    return weights


def _ensemble_weights(snap: pd.DataFrame, prices_full: pd.DataFrame, ensemble: str) -> np.ndarray:
    """
    Run the named signal ensemble against the full price panel and convert
    the resulting cross-sectional scores into portfolio weights.
    """
    from src.strategies.etf.ensemble import (
        default_ensemble,
        momentum_heavy_ensemble,
        reversion_heavy_ensemble,
    )

    factory = {
        "ensemble_default": default_ensemble,
        "ensemble_momentum_heavy": momentum_heavy_ensemble,
        "ensemble_reversion_heavy": reversion_heavy_ensemble,
    }.get(ensemble)
    if factory is None:
        raise ValueError(f"Unknown ensemble '{ensemble}'")

    ens = factory()
    # the rebalance date: a symbol a session behind has an older row
    as_of = pd.to_datetime(snap["date"]).max()
    scored = ens.compute(prices_full, as_of)
    score_map = (
        dict(zip(scored["symbol"].astype(str).str.upper(), scored["score"]))
        if not scored.empty
        else {}
    )
    if not score_map:
        # Ensemble couldn't score anything — fall back to equal weight rather
        # than crashing the basket build.
        return _equal_weight(snap)
    return _ensemble_score_to_weights(snap, score_map)


def _apply_optimizer(
    snap: pd.DataFrame,
    optimizer: str,
    *,
    top_k: int,
    momentum_tilt: float,
    prices_full: pd.DataFrame | None = None,
) -> np.ndarray:
    if optimizer == "equal_weight":
        return _equal_weight(snap)
    if optimizer == "inverse_vol":
        return _inverse_vol(snap)
    if optimizer == "risk_parity":
        return _risk_parity(snap, returns_history=pd.DataFrame())
    if optimizer == "momentum_weighted":
        return _momentum_weighted(snap)
    if optimizer == "momentum_tilted":
        return _momentum_tilted(snap, tilt=momentum_tilt)
    if optimizer == "momentum_top5":
        return _momentum_top5(snap, top_k=top_k)
    if optimizer.startswith("ensemble_"):
        if prices_full is None or prices_full.empty:
            return _equal_weight(snap)
        return _ensemble_weights(snap, prices_full, optimizer)
    raise ValueError(
        f"Unknown optimizer '{optimizer}'. Available: {OPTIMIZERS_AVAILABLE}"
    )


# ── Target builder ──────────────────────────────────────────────────────────

def _build_targets(
    df: pd.DataFrame,
    *,
    optimizer: str,
    top_k: int,
    momentum_tilt: float,
) -> pd.DataFrame:
    # Reconstitute a long-format prices DataFrame from the feature panel for
    # the signal ensembles. Keeps the contract clean: ensembles ingest the
    # same panel they'd see standalone.
    prices_full = df[["date", "symbol", "close"]].copy()

    rows: list[pd.DataFrame] = []
    # One rebalance a month, on the panel's last session that month. Each
    # symbol enters at its last row on or before that date, up to
    # MAX_LAG_SESSIONS sessions old, so one missing bar (a failed fetch) keeps
    # a symbol in the targets at its last close. Dropped from the latest
    # targets, it would be sold whole as a holding outside the model.
    by_date = df.sort_values(["date", "symbol"])
    rebal_dates = sorted(df.groupby("month")["date"].max().tolist())

    for rebal_date in rebal_dates:
        recent = by_date[by_date["date"].between(rebal_date - pd.Timedelta(days=14), rebal_date)]
        snap = recent.groupby("symbol").tail(1)
        lag = np.busday_count(snap["date"].to_numpy().astype("datetime64[D]"), np.datetime64(rebal_date.date()))
        snap = snap[(lag <= MAX_LAG_SESSIONS) & snap["eligible"]].copy()
        if snap.empty:
            continue

        snap = snap.sort_values(["mom", "symbol"], ascending=[False, True]).reset_index(drop=True)
        snap["rank"] = np.arange(1, len(snap) + 1)

        weights = _apply_optimizer(
            snap,
            optimizer,
            top_k=top_k,
            momentum_tilt=momentum_tilt,
            prices_full=prices_full,
        )
        snap["target_weight"] = weights

        # Drop zero-weight rows so target file stays compact when an optimizer
        # excludes some assets (e.g. momentum_top5).
        held = snap[snap["target_weight"] > 0].copy()
        if held.empty:
            continue

        held["rebalance_date"] = rebal_date
        held["signal_value"] = held["mom"]

        rows.append(
            held[
                [
                    "rebalance_date",
                    "symbol",
                    "target_weight",
                    "signal_value",
                    "rank",
                    "close",
                    "obs_count",
                ]
            ].rename(columns={"close": "reference_price"})
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "rebalance_date",
                "symbol",
                "target_weight",
                "signal_value",
                "rank",
                "reference_price",
                "obs_count",
            ]
        )

    out = pd.concat(rows, ignore_index=True)
    out = out.sort_values(["rebalance_date", "rank", "symbol"]).reset_index(drop=True)
    return out


def generate_targets(
    profile: str = "paper",
    momentum_lookback: int = MOMENTUM_LOOKBACK_DEFAULT,
    min_history: int = MIN_HISTORY_DEFAULT,
    top_k: int = 5,
    optimizer: str | None = None,
    momentum_tilt: float = 0.5,
    universe: Iterable[str] | None = None,
    through: date | None = None,
) -> dict[str, Any]:
    """Build the monthly target file from the model universe's prices.

    ``optimizer`` resolution order:  function arg → CAPITALFUND_OPTIMIZER env
    → DEFAULT_OPTIMIZER. ``universe`` defaults to :func:`model_universe`.
    Prices are read up to ``through``, by default the last session whose
    daily bar is final (src/common/clock.py): a run before the close builds
    targets from yesterday's close even if the master holds a partial bar
    for today.
    """
    if optimizer is None:
        optimizer = os.environ.get("CAPITALFUND_OPTIMIZER", DEFAULT_OPTIMIZER)
    if optimizer not in OPTIMIZERS_AVAILABLE:
        raise ValueError(
            f"Unknown optimizer '{optimizer}'. Available: {OPTIMIZERS_AVAILABLE}"
        )

    p = _paths()
    p["targets_csv"].parent.mkdir(parents=True, exist_ok=True)

    universe = tuple(s.strip().upper() for s in (universe or model_universe()))
    if through is None:
        from src.common.clock import last_completed_session

        through = last_completed_session()
    prices = _load_prices(universe)
    prices = prices[prices["date"] <= pd.Timestamp(through)]
    if prices.empty:
        raise ValueError(f"The master price file has no rows for the model universe: {', '.join(universe)}")
    in_master = set(prices["symbol"])
    features = _build_features(
        prices,
        momentum_lookback=momentum_lookback,
        min_history=min_history,
    )
    targets = _build_targets(
        features,
        optimizer=optimizer,
        top_k=top_k,
        momentum_tilt=momentum_tilt,
    )

    targets.to_csv(p["targets_csv"], index=False)
    targets.to_parquet(p["targets_parquet"], index=False)

    latest_rebal = None
    if not targets.empty:
        latest_rebal = str(pd.to_datetime(targets["rebalance_date"]).max().date())

    preview = targets.tail(20).to_dict(orient="records")

    result = TargetResult(
        ok=True,
        profile=profile,
        action="generate_targets",
        rows_written=len(targets),
        rebalance_dates=int(targets["rebalance_date"].nunique()) if not targets.empty else 0,
        symbols_considered=int(prices["symbol"].nunique()),
        optimizer=optimizer,
        latest_rebalance_date=latest_rebal,
        csv_path=str(p["targets_csv"]),
        parquet_path=str(p["targets_parquet"]),
        preview=preview,
        universe=list(universe),
        universe_missing=[s for s in universe if s not in in_master],
        prices_through=str(prices["date"].max().date()),
    )
    return asdict(result)


def run(**kwargs: Any) -> dict[str, Any]:
    return generate_targets(**kwargs)


def main(**kwargs: Any) -> dict[str, Any]:
    return generate_targets(**kwargs)
