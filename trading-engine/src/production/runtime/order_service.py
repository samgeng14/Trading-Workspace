"""The order service: the duplicate-submission guard around submit_paper_orders.

The daily runner's submit step and the dashboard's API go through it. It lived
in the old Streamlit dashboard's package; the runner shouldn't depend on that,
so it sits with the runtime now.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import polars as pl


TRADING_ENGINE_ROOT = Path(__file__).resolve().parents[3]
BROKER_ROOT = TRADING_ENGINE_ROOT / "data" / "broker"
ARTIFACTS_ROOT = TRADING_ENGINE_ROOT / "artifacts"

ORDERS_DIR = BROKER_ROOT / "orders"
FILLS_DIR = BROKER_ROOT / "fills"
RECON_DIR = BROKER_ROOT / "reconciliations"
BASKETS_DIR = ARTIFACTS_ROOT / "baskets"
RUNS_DIR = ARTIFACTS_ROOT / "runs"


def _run_maybe_async(value: Any) -> Any:
    if inspect.isawaitable(value):
        try:
            return asyncio.run(value)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(value)
            finally:
                loop.close()
    return value


def _call_with_supported_kwargs(func: Any, **kwargs: Any) -> Any:
    sig = inspect.signature(func)
    params = sig.parameters

    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if accepts_kwargs:
        return _run_maybe_async(func(**kwargs))

    filtered = {k: v for k, v in kwargs.items() if k in params}
    return _run_maybe_async(func(**filtered))


def _invoke_first(module_path: str, candidate_names: list[str], **kwargs: Any) -> Any:
    module = importlib.import_module(module_path)

    for name in candidate_names:
        obj = getattr(module, name, None)
        if callable(obj):
            return _call_with_supported_kwargs(obj, **kwargs)

    raise AttributeError(
        f"No callable found in {module_path}. Tried: {candidate_names}"
    )


def _read_table(path: Path | None) -> pl.DataFrame | None:
    if path is None or not path.exists():
        return None
    if path.suffix == ".csv":
        return pl.read_csv(path)
    if path.suffix == ".parquet":
        return pl.read_parquet(path)
    return None


def _latest_file(candidates: list[Path]) -> Path | None:
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def _latest_matching(directory: Path, pattern: str) -> Path | None:
    return _latest_file(list(directory.glob(pattern)))


def _safe_iso_mtime(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return __import__("datetime").datetime.fromtimestamp(path.stat().st_mtime).isoformat(
        timespec="seconds"
    )


def latest_basket_path() -> Path | None:
    return _latest_file(
        [
            ORDERS_DIR / "paper_orders_only.parquet",
            ORDERS_DIR / "paper_orders_only.csv",
            ORDERS_DIR / "paper_order_blotter.parquet",
            ORDERS_DIR / "paper_order_blotter.csv",
            BASKETS_DIR / "orders_only.parquet",
            BASKETS_DIR / "orders_only.csv",
        ]
    )


def latest_submission_path() -> Path | None:
    return _latest_matching(ORDERS_DIR, "paper_orders_submitted_*.csv")


def latest_fill_log_path() -> Path | None:
    return _latest_matching(FILLS_DIR, "paper_trade_log_*.csv")


def latest_reconciliation_path() -> Path | None:
    return _latest_file(
        [
            RECON_DIR / "paper_reconciliation.parquet",
            RECON_DIR / "paper_reconciliation.csv",
        ]
    )


def load_latest_basket() -> pl.DataFrame | None:
    return _read_table(latest_basket_path())


def load_latest_submission_log() -> pl.DataFrame | None:
    return _read_table(latest_submission_path())


def load_latest_fill_log() -> pl.DataFrame | None:
    return _read_table(latest_fill_log_path())


def load_latest_reconciliation() -> pl.DataFrame | None:
    return _read_table(latest_reconciliation_path())


def recent_order_files(limit: int = 10) -> list[str]:
    files = sorted(
        ORDERS_DIR.glob("paper_orders_submitted_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [str(p) for p in files[:limit]]


def recent_fill_files(limit: int = 10) -> list[str]:
    files = sorted(
        FILLS_DIR.glob("paper_trade_log_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [str(p) for p in files[:limit]]


def _basket_fingerprint(basket_path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(basket_path.read_bytes())
    return hasher.hexdigest()


def submit_lock_path(profile: str = "paper") -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR / f"{profile}_submit_lock.json"


def read_submit_lock(profile: str = "paper") -> dict[str, Any] | None:
    path = submit_lock_path(profile)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def clear_submit_lock(profile: str = "paper") -> None:
    path = submit_lock_path(profile)
    if path.exists():
        path.unlink()


def write_submit_lock(profile: str, basket_path: Path, submission_path: Path | None) -> dict[str, Any]:
    payload = {
        "profile": profile,
        "basket_path": str(basket_path),
        "basket_mtime": basket_path.stat().st_mtime,
        "basket_fingerprint": _basket_fingerprint(basket_path),
        "submission_path": str(submission_path) if submission_path else None,
        "submission_mtime": submission_path.stat().st_mtime if submission_path and submission_path.exists() else None,
    }
    path = submit_lock_path(profile)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def duplicate_submission_guard(profile: str = "paper", basket_path: Path | None = None) -> dict[str, Any]:
    basket = basket_path or latest_basket_path()
    if basket is None or not basket.exists():
        return {
            "ok": False,
            "allowed": False,
            "reason": "No basket file found.",
            "basket_path": None,
        }

    fingerprint = _basket_fingerprint(basket)
    lock = read_submit_lock(profile)

    if lock and lock.get("basket_fingerprint") == fingerprint:
        return {
            "ok": True,
            "allowed": False,
            "reason": "This basket fingerprint was already submitted.",
            "basket_path": str(basket),
            "basket_fingerprint": fingerprint,
            "lock": lock,
        }

    return {
        "ok": True,
        "allowed": True,
        "reason": "Basket is eligible for submission.",
        "basket_path": str(basket),
        "basket_fingerprint": fingerprint,
        "lock": lock,
    }


def submit_paper_orders(
    profile: str = "paper",
    force: bool = False,
    *,
    dry_run: bool = False,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if profile != "paper":
        raise ValueError("submit_paper_orders service only allows profile='paper'.")

    basket_before = latest_basket_path()
    submit_before = latest_submission_path()
    fills_before = latest_fill_log_path()

    guard = duplicate_submission_guard(profile=profile, basket_path=basket_before)
    if not force and not guard["allowed"]:
        return {
            "ok": False,
            "submitted": False,
            "reason": guard["reason"],
            "guard": guard,
            "submission_path": str(submit_before) if submit_before else None,
            "fill_log_path": str(fills_before) if fills_before else None,
        }

    # The runtime ``submit_paper_orders`` function takes a single
    # ``SubmitPaperOrdersConfig`` argument, not ``profile=`` kwargs. Build
    # the config here so all callers (CLI, dashboard, daily_runner) end up
    # in the same code path. default_config() already resolves host, port
    # and the fixed "orders" clientId from env / config/broker.yaml.
    from src.production.runtime.submit_paper_orders import (
        default_config,
        submit_paper_orders as _runtime_submit,
    )

    cfg = default_config()
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = int(port)
    if client_id is not None:
        cfg.client_id = int(client_id)
    cfg.dry_run = bool(dry_run)

    # Filter additional kwargs to fields actually on the dataclass.
    cfg_fields = {f for f in cfg.__dataclass_fields__}
    for k, v in list(kwargs.items()):
        if k in cfg_fields:
            setattr(cfg, k, v)

    result = _runtime_submit(cfg)

    if result.get("blocked"):
        # the kill switch or the no-leverage check inside submit_paper_orders: nothing went out
        return {
            "ok": False,
            "submitted": False,
            "blocked": True,
            "reasons": list(result.get("reasons") or []),
            "reason": "; ".join(result.get("reasons") or []),
            "profile": profile,
            "result": result,
            "basket_path": str(basket_before) if basket_before else None,
            "submission_path": str(submit_before) if submit_before else None,
            "fill_log_path": str(fills_before) if fills_before else None,
        }

    submit_after = latest_submission_path()
    fills_after = latest_fill_log_path()

    # the lock marks a basket as sent; a dry run's preview file doesn't send it
    submitted = bool(result.get("submitted"))
    if submitted and basket_before and submit_after:
        write_submit_lock(profile=profile, basket_path=basket_before, submission_path=submit_after)

    return {
        "ok": True,
        "submitted": submitted,
        "profile": profile,
        "result": result,
        "basket_path": str(basket_before) if basket_before else None,
        "submission_path": str(submit_after) if submit_after else None,
        "submission_timestamp": _safe_iso_mtime(submit_after),
        "fill_log_path": str(fills_after) if fills_after else None,
        "fill_log_timestamp": _safe_iso_mtime(fills_after),
        "previous_submission_path": str(submit_before) if submit_before else None,
        "previous_fill_log_path": str(fills_before) if fills_before else None,
    }


def get_order_status(profile: str = "paper") -> dict[str, Any]:
    basket = latest_basket_path()
    submission = latest_submission_path()
    fills = latest_fill_log_path()
    reconciliation = latest_reconciliation_path()
    guard = duplicate_submission_guard(profile=profile, basket_path=basket)

    basket_df = load_latest_basket()
    submission_df = load_latest_submission_log()
    fill_df = load_latest_fill_log()
    recon_df = load_latest_reconciliation()

    return {
        "profile": profile,
        "basket_path": str(basket) if basket else None,
        "basket_timestamp": _safe_iso_mtime(basket),
        "basket_rows": 0 if basket_df is None else basket_df.height,
        "submission_path": str(submission) if submission else None,
        "submission_timestamp": _safe_iso_mtime(submission),
        "submission_rows": 0 if submission_df is None else submission_df.height,
        "fill_log_path": str(fills) if fills else None,
        "fill_log_timestamp": _safe_iso_mtime(fills),
        "fill_rows": 0 if fill_df is None else fill_df.height,
        "reconciliation_path": str(reconciliation) if reconciliation else None,
        "reconciliation_timestamp": _safe_iso_mtime(reconciliation),
        "reconciliation_rows": 0 if recon_df is None else recon_df.height,
        "submit_guard": guard,
    }