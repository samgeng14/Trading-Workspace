"""
sidebar.py - Routes for the global sidebar: profile, optimizer, master price
metadata, ETF refresh, and the Run-Audit button.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _rotating_client_id(base: int, span: int) -> int:
    """Return a clientId in [base, base+span) that rotates each second.

    Avoids the IBKR Error 326 ("client id is already in use") collision when
    a previous run's connection has not been fully released yet — every back-
    to-back click lands on a different slot.
    """
    return base + (int(time.time()) % span)

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.dashboard._cache import cache_data
from src.dashboard.routers._helpers import (
    get_optimizer,
    get_profile,
    set_optimizer,
    set_profile,
)


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ── State endpoints ──────────────────────────────────────────────────────────

class ProfileBody(BaseModel):
    profile: str


class OptimizerBody(BaseModel):
    optimizer: str


@router.get("/state")
def read_state() -> dict[str, Any]:
    optimizers, default = _list_optimizers()
    return {
        "profile": get_profile(),
        "optimizer": get_optimizer(),
        "optimizers": optimizers,
        "optimizer_default": default,
        "optimizer_descriptions": _OPTIMIZER_DESCRIPTIONS,
        "master": _master_meta(),
    }


@router.post("/profile")
def write_profile(body: ProfileBody) -> dict[str, Any]:
    try:
        set_profile(body.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "profile": get_profile()}


@router.post("/optimizer")
def write_optimizer(body: OptimizerBody) -> dict[str, Any]:
    optimizers, _default = _list_optimizers()
    if body.optimizer not in optimizers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown optimizer {body.optimizer!r}. Allowed: {optimizers}",
        )
    set_optimizer(body.optimizer)
    return {"ok": True, "optimizer": get_optimizer()}


# ── Master price file metadata ───────────────────────────────────────────────

@router.get("/master-meta")
def master_meta() -> dict[str, Any]:
    return _master_meta()


def _master_meta() -> dict[str, Any]:
    base = PROJECT_ROOT / "data" / "market" / "cleaned" / "prices"
    parquet = base / "etf_prices_master.parquet"
    csv = base / "etf_prices_master.csv"
    path = parquet if parquet.exists() else (csv if csv.exists() else None)
    if path is None:
        return {"latest_date": None, "mtime": None, "path": None, "found": False}
    try:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path, columns=["date"])
        else:
            df = pd.read_csv(path, usecols=["date"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        latest = df["date"].max()
        latest_str = latest.strftime("%Y-%m-%d") if latest is not pd.NaT else None
        mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        return {
            "latest_date": latest_str,
            "mtime": mtime,
            "path": str(path),
            "found": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"latest_date": None, "mtime": None, "path": str(path), "error": str(exc), "found": True}


# ── Cache invalidation ───────────────────────────────────────────────────────

@router.post("/cache/clear")
def clear_cache() -> dict[str, bool]:
    cache_data.clear()
    return {"ok": True}


# ── ETF refresh + Run Audit ─────────────────────────────────────────────────

@router.post("/refresh-etf")
def refresh_etf(profile: str | None = None) -> dict[str, Any]:
    profile = profile or get_profile()
    try:
        from src.production.pipeline.append_ibkr_daily import append_ibkr_daily
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not import append_ibkr_daily: {exc}") from exc

    port = _autodetect_ibkr_port(profile)
    # Rotate clientId each second so rapid re-clicks don't collide on Error 326.
    # 150–189 range keeps refresh-etf separate from run-audit's 200+ range.
    client_id = _rotating_client_id(base=150, span=40)
    try:
        result = append_ibkr_daily(
            profile=profile,
            host="127.0.0.1",
            port=port,
            client_id=client_id,
            lookback="10 D",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "port": port}

    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "unknown error"), "port": port}

    cache_data.clear()
    return {
        "ok": True,
        "port": port,
        "new_rows": result.get("new_rows_added_to_master", 0),
        "symbols_with_data": result.get("symbols_with_data", 0),
        "latest_date": result.get("latest_date"),
        "message": (
            f"Appended {result.get('new_rows_added_to_master', 0)} new rows for "
            f"{result.get('symbols_with_data', 0)} symbols. "
            f"Latest: {result.get('latest_date')} (port {port})."
        ),
    }


@router.post("/run-audit")
def run_audit(profile: str | None = None) -> dict[str, Any]:
    profile = profile or get_profile()
    try:
        from src.production.daily_runner import run as daily_run
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not import daily_runner: {exc}") from exc

    port = _autodetect_ibkr_port(profile)
    # Rotate clientId each second so rapid re-clicks don't collide on Error 326.
    # daily_runner uses client_id, client_id+10, client_id+20 internally, so
    # the effective range is 200..289 — well above any default IBKR slot.
    client_id = _rotating_client_id(base=200, span=50)
    try:
        result = daily_run(
            host="127.0.0.1",
            port=port,
            client_id=client_id,
            profile=profile,
            lookback="10 D",
            dry_run=True,
            force_rebalance=True,
            skip_append=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "port": port}

    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "(see daily_runner log)",
            "port": port,
        }

    cache_data.clear()
    steps = result.get("steps", {})
    append = steps.get("append_daily", {}) or {}
    targets = steps.get("generate_targets", {}) or {}
    basket = steps.get("build_basket", {}) or {}

    parts = []
    if append.get("ok"):
        parts.append(
            f"+{append.get('new_rows_added_to_master', 0)} bars "
            f"(latest {append.get('latest_date')})"
        )
    if targets.get("ok"):
        parts.append(f"targets latest {targets.get('latest_rebalance_date')}")
    if basket:
        parts.append(f"basket rows: {basket.get('reconciliation_rows', '-')}")
    return {
        "ok": True,
        "port": port,
        "summary": " - ".join(parts) if parts else "audit complete",
        "steps": steps,
    }


# ── Internals ───────────────────────────────────────────────────────────────

_OPTIMIZER_DESCRIPTIONS = {
    "equal_weight": "1/N - every ETF same weight",
    "inverse_vol": "1/sigma - diversified, lower-vol gets more (default)",
    "risk_parity": "Equal risk contribution (zero-correlation approx)",
    "momentum_weighted": "Hold only positive-momentum ETFs, weight by signal",
    "momentum_tilted": "Equal-weight tilted toward winners (tilt=0.5)",
    "momentum_top5": "Legacy: top-5 by momentum, 20% each",
}


def _list_optimizers() -> tuple[list[str], str]:
    try:
        from src.production.generate_targets import (
            DEFAULT_OPTIMIZER,
            OPTIMIZERS_AVAILABLE,
        )
        return list(OPTIMIZERS_AVAILABLE), DEFAULT_OPTIMIZER
    except Exception:  # noqa: BLE001
        return ["inverse_vol", "equal_weight", "momentum_top5"], "inverse_vol"


def _autodetect_ibkr_port(profile: str) -> int:
    # Only ever probes ports for the same profile; see settings.candidate_ports.
    from src.broker.ibkr.settings import detect_port

    return detect_port(profile)
