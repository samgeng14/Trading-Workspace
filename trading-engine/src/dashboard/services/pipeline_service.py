"""
pipeline_service.py — Pipeline orchestration service for the dashboard.

Uses lazy imports so Streamlit never crashes on startup due to missing deps.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _latest_matching(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _ts(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def get_pipeline_status(profile: str = "paper") -> dict[str, Any]:
    root = _project_root()

    prices_path   = root / "data" / "market" / "cleaned" / "prices"  / "etf_prices_master.parquet"
    targets_path  = root / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet"
    snapshot_path = _latest_matching(root / "data" / "market" / "snapshots", "ibkr_daily_append_*.csv")

    # Lazy import to avoid circular deps
    try:
        from src.production.runtime.order_service import (
            latest_basket_path,
            latest_fill_log_path,
            latest_submission_path,
        )
        basket_path     = latest_basket_path()
        submission_path = latest_submission_path()
        fill_log_path   = latest_fill_log_path()
    except Exception:
        basket_path = submission_path = fill_log_path = None

    return {
        "profile":              profile,
        "prices_path":          str(prices_path)   if prices_path.exists()  else None,
        "targets_path":         str(targets_path)  if targets_path.exists() else None,
        "snapshot_path":        str(snapshot_path) if snapshot_path         else None,
        "basket_path":          str(basket_path)   if basket_path           else None,
        "submission_path":      str(submission_path) if submission_path     else None,
        "fill_log_path":        str(fill_log_path) if fill_log_path         else None,
        "prices_timestamp":     _ts(prices_path),
        "targets_timestamp":    _ts(targets_path),
        "snapshot_timestamp":   _ts(snapshot_path),
        "basket_timestamp":     _ts(basket_path),
        "submission_timestamp": _ts(submission_path),
        "fill_log_timestamp":   _ts(fill_log_path),
    }


def append_ibkr_daily(
    profile: str = "paper",
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 101,
    lookback: str = "15 D",
) -> dict[str, Any]:
    try:
        from src.production.pipeline.append_ibkr_daily import (
            append_ibkr_daily as _fn,
        )
        return _fn(
            profile=profile,
            host=host,
            port=port,
            client_id=client_id,
            lookback=lookback,
        )
    except Exception as exc:
        return {"ok": False, "action": "append_ibkr_daily", "error": str(exc)}


def refresh_targets(
    profile: str = "paper",
    momentum_lookback: int = 126,
    min_history: int = 126,
    top_k: int = 5,
) -> dict[str, Any]:
    try:
        from src.production.generate_targets import generate_targets as _fn
        return _fn(
            profile=profile,
            momentum_lookback=momentum_lookback,
            min_history=min_history,
            top_k=top_k,
        )
    except Exception as exc:
        return {"ok": False, "action": "generate_targets", "error": str(exc)}


def run_daily_pipeline(
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 101,
    profile: str = "paper",
    lookback: str = "5 D",
    dry_run: bool = True,
    force_rebalance: bool = False,
) -> dict[str, Any]:
    """Run the full daily pipeline (append → targets → basket → submit)."""
    try:
        from src.production.daily_runner import run as _run
        return _run(
            host=host,
            port=port,
            client_id=client_id,
            profile=profile,
            lookback=lookback,
            dry_run=dry_run,
            force_rebalance=force_rebalance,
        )
    except Exception as exc:
        return {"ok": False, "action": "daily_pipeline", "error": str(exc)}
