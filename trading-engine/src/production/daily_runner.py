#!/usr/bin/env python3
"""
daily_runner.py — Daily auto-trade pipeline.

Run this at 4:30 PM ET (after market close) every trading day.
It handles the full cycle automatically:

  1. Append today's IBKR daily bars to the master price file
  2. Regenerate targets (momentum model — picks top-5 ETFs monthly)
  3. Detect if today is a rebalance date (end-of-month or new signal, drift,
     or shares held outside the model, which the basket sells)
  4. If rebalance needed: build basket (diff vs current positions)
  5. Submit paper orders to IBKR
  6. Write structured log to data/logs/runtime/

Usage:
    cd /Users/tradingworkspace/TradingWorkspace/trading-engine
    python -m src.production.daily_runner
    python -m src.production.daily_runner --dry-run         # skip order submission
    python -m src.production.daily_runner --force-rebalance # always rebalance today
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:
    import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# ── Logging ──────────────────────────────────────────────────────────────────

LOG_DIR = PROJECT_ROOT / "data" / "logs" / "runtime"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_path = LOG_DIR / f"daily_runner_{_ts_file}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_path),
    ],
)
log = logging.getLogger("daily_runner")

# ── Config ────────────────────────────────────────────────────────────────────

from src.broker.ibkr.settings import client_id_for, resolve_endpoint

# Host / ports come from env (IBKR_HOST, IBKR_PORT_PAPER, IBKR_PORT_LIVE)
# or config/broker.yaml — see src/broker/ibkr/settings.py. Works for IB
# Gateway (4002 paper / 4001 live) and TWS (7497 / 7496). CLI --port wins.
DEFAULT_HOST = resolve_endpoint("paper").host
DEFAULT_PORT_PAPER = resolve_endpoint("paper").port
DEFAULT_PORT_LIVE = resolve_endpoint("live").port
DEFAULT_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "101"))

# Momentum strategy params
MOMENTUM_LOOKBACK = 126   # trading days (~6 months)
MIN_HISTORY = 126
TOP_K = 5                 # number of ETFs to hold at a time

# Default rebalance config (used as a fallback if execution.yaml is missing
# or doesn't contain a ``rebalance:`` block). Real values are sourced from
# config/execution.yaml so they can be tuned without code changes — see the
# `rebalance:` section in that file for documented thresholds.
DEFAULT_REBALANCE_CONFIG: dict[str, Any] = {
    "cadence": "monthly",                                  # monthly | drift
    "drift_threshold": 0.05,                               # 5% per-name band
    "composition_threshold": 0.05,                         # materiality cutoff
    "cooldown_days": 3,                                    # global cooldown
    "state_path": "data/broker/state/last_rebalance.json",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> date:
    return datetime.now().date()


def _is_month_end_week() -> bool:
    """True if today is in the last 5 calendar days of the month."""
    d = _today()
    import calendar
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.day >= last_day - 4


def _load_rebalance_config() -> dict[str, Any]:
    """Load the rebalance section from config/execution.yaml.

    Falls back to ``DEFAULT_REBALANCE_CONFIG`` for any missing keys so
    that an incomplete config never silently disables a safety knob.
    """
    cfg = dict(DEFAULT_REBALANCE_CONFIG)
    path = PROJECT_ROOT / "config" / "execution.yaml"
    if not path.exists():
        log.info("No execution.yaml found → using default rebalance config.")
        return cfg
    try:
        import yaml  # type: ignore[import-not-found]
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
        rb = (doc.get("execution") or {}).get("rebalance") or {}
        for k, default_v in DEFAULT_REBALANCE_CONFIG.items():
            if k in rb and rb[k] is not None:
                cfg[k] = rb[k]
    except Exception as exc:
        log.warning("Could not parse execution.yaml rebalance block: %s", exc)
    return cfg


def _load_latest_targets() -> "pd.DataFrame | None":
    """Load the most recent rebalance date from the targets file."""
    try:
        import pandas as pd
        path = PROJECT_ROOT / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet"
        if not path.exists():
            path = path.with_suffix(".csv")
        if not path.exists():
            return None
        df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        df["rebalance_date"] = pd.to_datetime(df["rebalance_date"]).dt.date
        return df
    except Exception as exc:
        log.warning("Could not load targets: %s", exc)
        return None


def _latest_rebalance_date(targets_df: "pd.DataFrame") -> date | None:
    try:
        return targets_df["rebalance_date"].max()
    except Exception:
        return None


def _load_reconciliation(profile: str) -> "pd.DataFrame | None":
    """Load the latest reconciliation (target + current per symbol).

    The reconciliation file is rebuilt on every run by the basket-build
    step and contains everything we need to compute drift: ``symbol``,
    ``target_weight``, ``current_shares``, and ``close``.
    """
    try:
        import pandas as pd
        base = PROJECT_ROOT / "data" / "broker" / "reconciliations"
        path = base / f"{profile}_reconciliation.parquet"
        if not path.exists():
            path = path.with_suffix(".csv")
        if not path.exists():
            return None
        return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    except Exception as exc:
        log.warning("Could not load reconciliation for %s: %s", profile, exc)
        return None


def _weights_from_reconciliation(
    recon_df: "pd.DataFrame",
) -> tuple[dict[str, float], dict[str, float]]:
    """Return ``(target_weights, current_weights)`` from a reconciliation.

    NAV is inferred from ``target_dollars / target_weight`` (their ratio
    is the implied total NAV, ignoring the cash buffer). Current weights
    are then ``current_shares * close / nav``. Robust to the small float
    noise from the cash buffer because we use the median ratio.
    """
    target_weights = {
        str(row["symbol"]): float(row["target_weight"])
        for _, row in recon_df.iterrows()
        if float(row.get("target_weight", 0.0)) > 0
    }

    nav: float = 0.0
    try:
        ratios = (
            recon_df.loc[recon_df["target_weight"] > 0, "target_dollars"]
            / recon_df.loc[recon_df["target_weight"] > 0, "target_weight"]
        )
        nav = float(ratios.median())
    except Exception:
        nav = 0.0

    current_weights: dict[str, float] = {}
    if nav > 0:
        for _, row in recon_df.iterrows():
            sym = str(row["symbol"])
            shares = float(row.get("current_shares", 0.0) or 0.0)
            close = float(row.get("close", 0.0) or 0.0)
            if shares != 0 and close > 0:
                current_weights[sym] = (shares * close) / nav

    return target_weights, current_weights


def _current_positions(
    profile: str, host: str, port: int, client_id: int,
) -> tuple["pd.DataFrame | None", str]:
    """Every position in the account and where it came from: IBKR now, else
    the last positions snapshot. The snapshot is written when a basket is
    built, before its orders fill, so it is only the fallback."""
    import pandas as pd

    try:
        from src.broker.ibkr.client import IBKRClient, IBKRConnectionConfig
        from src.production.runtime.build_paper_basket import fetch_positions_df

        client = IBKRClient(
            IBKRConnectionConfig(host=host, port=port, client_id=client_id, expected_profile=profile)
        )
        client.connect()
        try:
            return fetch_positions_df(client), "ibkr"
        finally:
            client.disconnect()
    except Exception as exc:
        log.warning("Could not read positions from IBKR (%s: %s); using the last snapshot.", type(exc).__name__, exc)
    path = PROJECT_ROOT / "data" / "broker" / "positions" / f"{profile}_positions_snapshot.csv"
    if path.exists():
        return pd.read_csv(path), "snapshot"
    return None, "none"


def _holdings_outside_model(
    targets_df: "pd.DataFrame | None",
    profile: str,
    host: str,
    port: int,
    client_id: int,
) -> dict[str, Any]:
    """Shares the account holds that the latest targets don't include (the
    basket sells them whole) and holdings it leaves alone (futures, options).
    JSON-safe, for the run log."""
    from src.production.generate_targets import model_universe
    from src.production.runtime.build_paper_basket import holdings_outside_model

    out: dict[str, Any] = {"sell": [], "kept": [], "warnings": [], "positions_from": "none"}
    if targets_df is None or targets_df.empty:
        return out
    latest = targets_df.loc[targets_df["rebalance_date"] == targets_df["rebalance_date"].max()]
    latest = latest.loc[latest["symbol"].astype(str).str.upper().isin(model_universe())]
    positions, source = _current_positions(profile, host, port, client_id)
    out["positions_from"] = source
    if positions is None:
        return out
    outside = holdings_outside_model(latest, positions)
    out.update(sell=outside.sell, kept=outside.kept, warnings=outside.warnings())
    if outside.sell:
        log.info("Holdings outside the model (positions from %s): %s", source, ", ".join(outside.sell))
    for warning in out["warnings"]:
        log.warning("%s", warning)
    return out


def _load_last_rebalance_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Could not read last_rebalance state at %s: %s", state_path, exc)
        return None


def _save_last_rebalance_state(
    state_path: Path,
    *,
    submitted_at: date,
    target_weights: dict[str, float],
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "submitted_at": submitted_at.isoformat(),
        "basket": target_weights,
    }
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, state_path)
    log.info("Saved last_rebalance state → %s", state_path)


def _needs_rebalance_monthly(
    targets_df: "pd.DataFrame | None", force: bool = False
) -> bool:
    """Legacy monthly cadence: rebalance when the calendar month rolls over.

    This is the original gate. It fires only when (a) there's no targets
    file, (b) the latest signal is from a strictly earlier month, or
    (c) ``force=True``. Kept as a fallback / paper-safety mode behind
    ``cadence: monthly`` in ``execution.yaml``.
    """
    if force:
        log.info("Force-rebalance flag set → will submit orders.")
        return True

    if targets_df is None or targets_df.empty:
        log.info("No targets found → will rebalance.")
        return True

    latest = _latest_rebalance_date(targets_df)
    today = _today()

    if latest is None:
        return True

    # Tuple comparison handles the year-rollover edge case correctly
    # (e.g. latest=2025-12, today=2026-01) without the AND/OR mistake
    # the original line had.
    if (latest.year, latest.month) < (today.year, today.month):
        log.info(
            "Latest rebalance date %s is before current month (%s-%02d) → will rebalance.",
            latest, today.year, today.month,
        )
        return True

    if latest > today:
        # Future signal date → clock skew or replay; don't silently skip.
        log.warning(
            "Latest rebalance date %s is AFTER today %s — clock anomaly, "
            "forcing rebalance to refresh.", latest, today,
        )
        return True

    log.info(
        "Latest rebalance date %s is current month → skipping order submission today.",
        latest,
    )
    return False


def _needs_rebalance_drift(
    profile: str,
    rb_cfg: dict[str, Any],
    force: bool = False,
    outside_model: Sequence[str] = (),
) -> tuple[bool, dict[str, Any]]:
    """Drift-band cadence: daily check, fire on holdings outside the model,
    composition or drift.

    Returns ``(should_rebalance, diagnostics)`` where ``diagnostics`` is
    a JSON-safe dict suitable for the run log.
    """
    from src.runtime.auto_rebalance import decide_rebalance_with_drift

    recon = _load_reconciliation(profile)
    if recon is None or recon.empty:
        log.info("No reconciliation found → will rebalance (cold start).")
        return True, {"reason": "no reconciliation file", "cold_start": True}

    target_weights, current_weights = _weights_from_reconciliation(recon)

    state_path = PROJECT_ROOT / rb_cfg["state_path"]
    state = _load_last_rebalance_state(state_path)

    last_basket: Optional[dict[str, float]] = None
    last_submitted_at: Optional[date] = None
    if state:
        try:
            last_basket = {str(k): float(v) for k, v in (state.get("basket") or {}).items()}
            if state.get("submitted_at"):
                last_submitted_at = date.fromisoformat(str(state["submitted_at"]))
        except Exception as exc:
            log.warning("Could not parse last_rebalance state: %s", exc)

    decision = decide_rebalance_with_drift(
        target_weights=target_weights,
        current_weights=current_weights,
        today=_today(),
        last_submitted_at=last_submitted_at,
        last_basket=last_basket,
        drift_threshold=float(rb_cfg["drift_threshold"]),
        composition_threshold=float(rb_cfg["composition_threshold"]),
        cooldown_days=int(rb_cfg["cooldown_days"]),
        force=force,
        outside_model=outside_model,
    )

    log.info(
        "Drift gate: rebalance=%s · max_drift=%.4f · composition_changed=%s · "
        "in_cooldown=%s · reason=%s",
        decision.rebalance,
        decision.max_drift,
        decision.composition_changed,
        decision.in_cooldown,
        decision.reason,
    )

    diagnostics = {
        "cadence": "drift",
        "rebalance": decision.rebalance,
        "reason": decision.reason,
        "max_drift": decision.max_drift,
        "composition_changed": decision.composition_changed,
        "in_cooldown": decision.in_cooldown,
        "cooldown_remaining_days": decision.cooldown_remaining_days,
        "drift_per_symbol": dict(decision.drift_per_symbol),
        "thresholds": {
            "drift": rb_cfg["drift_threshold"],
            "composition": rb_cfg["composition_threshold"],
            "cooldown_days": rb_cfg["cooldown_days"],
        },
    }
    return decision.rebalance, diagnostics


def _needs_rebalance(
    targets_df: "pd.DataFrame | None",
    profile: str,
    rb_cfg: dict[str, Any],
    force: bool = False,
    outside: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Cadence dispatcher.

    Returns ``(should_rebalance, diagnostics)``. The diagnostics dict is
    persisted in the run log so post-mortems can answer "why didn't the
    runner trade today?" without re-running anything.

    ``outside`` is :func:`_holdings_outside_model`. Shares held outside the
    model are a reason to rebalance under either cadence (the drift gate's
    cooldown still applies), since the basket is what sells them.
    """
    from src.runtime.auto_rebalance import outside_model_reason

    outside = outside or {}
    sell = list(outside.get("sell") or [])
    cadence = str(rb_cfg.get("cadence", "monthly")).lower()
    if cadence == "drift":
        decided, diag = _needs_rebalance_drift(profile=profile, rb_cfg=rb_cfg, force=force, outside_model=sell)
        if diag.get("cold_start") and sell:
            diag["reason"] += f"; {outside_model_reason(sell)}"
        return decided, {**diag, "outside_model": outside}
    if cadence != "monthly":
        log.warning("Unknown cadence %r in execution.yaml → falling back to monthly.", cadence)
    decided = _needs_rebalance_monthly(targets_df, force=force)
    diag: dict[str, Any] = {"cadence": "monthly", "rebalance": decided}
    if not decided and sell:
        log.info("Holdings outside the model → will rebalance; the basket sells them.")
        decided = True
        diag.update(rebalance=True, reason=outside_model_reason(sell))
    return decided, {**diag, "outside_model": outside}


def _write_run_log(run_id: str, result: dict[str, Any]) -> Path:
    run_dir = PROJECT_ROOT / "artifacts" / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / f"daily_run_{run_id}.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)
    log.info("Run log saved: %s", out)
    return out


def _already_ran_today_successfully() -> Optional[Path]:
    """Return the path of today's successful run log, if one exists.

    Used by the catch-up flag (``--skip-if-ran-today``) to make the
    LaunchAgent's RunAtLoad fire idempotent: log in any time after a
    successful 16:32 run and the runner exits without doing anything,
    but log in after a *missed* 16:32 (Mac was off, asleep, or you'd
    user-switched away too long) and it executes the catch-up.

    Dry runs don't count: the dashboard's "Run audit" button runs the
    pipeline with ``dry_run=True``, and that must not cancel the real
    scheduled run.
    """
    run_dir = PROJECT_ROOT / "artifacts" / "runs"
    if not run_dir.exists():
        return None
    today_prefix = f"daily_run_{_today().strftime('%Y%m%d')}_"
    for path in sorted(run_dir.glob(f"{today_prefix}*.json"), reverse=True):
        try:
            with open(path) as f:
                doc = json.load(f)
            if doc.get("ok") is True and not doc.get("dry_run"):
                return path
        except Exception:
            continue
    return None


# ── Step functions ────────────────────────────────────────────────────────────

def step_append_daily(
    host: str,
    port: int,
    client_id: int,
    profile: str,
    lookback: str = "5 D",
) -> dict[str, Any]:
    log.info("── Step 1: Append IBKR daily bars ──────────────────────────")
    from src.production.pipeline.append_ibkr_daily import append_ibkr_daily

    result = append_ibkr_daily(
        profile=profile,
        host=host,
        port=port,
        client_id=client_id,
        lookback=lookback,
    )

    if result.get("ok"):
        log.info(
            "✓ Appended %s new rows for %s symbols. Latest date: %s",
            result.get("new_rows_added_to_master", 0),
            result.get("symbols_with_data", 0),
            result.get("latest_date"),
        )
    else:
        log.error("✗ Append failed: %s", result.get("error"))

    return result


def step_append_indices_daily(
    host: str,
    port: int,
    client_id: int,
    profile: str,
    lookback: str = "5 D",
) -> dict[str, Any]:
    """Refresh indices_prices_master from IBKR. Fail-soft: errors logged, never aborts."""
    log.info("── Step 1b: Append IBKR indices (VIX, DXY) ─────────────────")
    try:
        from src.production.pipeline.append_indices_daily import append_indices_daily
        result = append_indices_daily(
            profile=profile, host=host, port=port, client_id=client_id, lookback=lookback,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft is intentional
        log.warning("Indices append raised — continuing pipeline. %s: %s", type(exc).__name__, exc)
        return {"ok": False, "action": "append_indices_daily", "error": str(exc), "fail_soft": True}

    if result.get("ok"):
        log.info(
            "✓ Indices: %s new rows · %s symbols · latest %s · errors=%s",
            result.get("new_rows_added_to_master", 0),
            result.get("symbols_with_data", 0),
            result.get("latest_date"),
            len(result.get("errors") or []),
        )
    else:
        log.warning("✗ Indices append failed (fail-soft): %s", result.get("error"))
    return result


def step_append_futures_daily(
    host: str,
    port: int,
    client_id: int,
    profile: str,
    lookback: str = "5 D",
) -> dict[str, Any]:
    """Refresh futures_prices_master from IBKR. Fail-soft: errors logged, never aborts."""
    log.info("── Step 1c: Append IBKR continuous futures ─────────────────")
    try:
        from src.production.pipeline.append_futures_daily import append_futures_daily
        result = append_futures_daily(
            profile=profile, host=host, port=port, client_id=client_id, lookback=lookback,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft is intentional
        log.warning("Futures append raised — continuing pipeline. %s: %s", type(exc).__name__, exc)
        return {"ok": False, "action": "append_futures_daily", "error": str(exc), "fail_soft": True}

    if result.get("ok"):
        log.info(
            "✓ Futures: %s new rows · %s symbols · latest %s · errors=%s",
            result.get("new_rows_added_to_master", 0),
            result.get("symbols_with_data", 0),
            result.get("latest_date"),
            len(result.get("errors") or []),
        )
    else:
        log.warning("✗ Futures append failed (fail-soft): %s", result.get("error"))
    return result


def step_generate_targets(profile: str) -> dict[str, Any]:
    log.info("── Step 2: Generate targets ─────────────────────────────────")
    from src.production.generate_targets import generate_targets

    optimizer = os.environ.get("CAPITALFUND_OPTIMIZER")  # None → use module default
    log.info(
        "  Optimizer: %s (override with CAPITALFUND_OPTIMIZER env)",
        optimizer or "default (inverse_vol)",
    )

    result = generate_targets(
        profile=profile,
        momentum_lookback=MOMENTUM_LOOKBACK,
        min_history=MIN_HISTORY,
        top_k=TOP_K,
        optimizer=optimizer,
    )

    if result.get("ok"):
        log.info(
            "✓ Targets written. Rebalance dates: %s. Latest: %s.",
            result.get("rebalance_dates", 0),
            result.get("latest_rebalance_date"),
        )
    else:
        log.error("✗ Target generation failed: %s", result.get("error"))

    return result


def step_build_basket(
    host: str,
    port: int,
    client_id: int,
    profile: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    log.info("── Step 3: Build basket ─────────────────────────────────────")
    try:
        from src.production.runtime.build_paper_basket import (
            default_config,
            BasketBuildConfig,
        )
        import dataclasses

        cfg = default_config()
        cfg = dataclasses.replace(cfg, host=host, port=port, client_id=client_id)

        # Import the actual build function dynamically
        import importlib
        mod = importlib.import_module("src.production.runtime.build_paper_basket")

        # Try to find the main build callable
        for name in ["build_paper_basket", "run", "main", "build"]:
            fn = getattr(mod, name, None)
            if callable(fn) and name != "default_config":
                result = fn(cfg)
                if isinstance(result, dict):
                    result.setdefault("ok", True)
                    result.setdefault("action", "build_basket")
                    return result
                return {"ok": True, "action": "build_basket", "result": str(result)}

        return {"ok": False, "action": "build_basket", "error": "No build function found"}

    except Exception as exc:
        log.exception("Build basket error")
        return {"ok": False, "action": "build_basket", "error": str(exc)}


def step_submit_orders(
    host: str,
    port: int,
    client_id: int,
    profile: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    log.info("── Step 4: Submit paper orders ──────────────────────────────")

    # ── Pre-trade risk gate ──────────────────────────────────────────────
    # Runs the kill switch + price validators + exposure limits before any
    # network call to IBKR, then the C++ no-leverage check on the whole
    # book (every holding, the model's or not, plus these orders). Either
    # one blocks. Runs on dry runs too, so the log says what a real run
    # would do. Aborts with a structured reason if anything fails so the
    # run log makes the failure cause obvious.
    try:
        import pandas as pd
        from src.runtime.leverage import check_leverage
        from src.runtime.risk_checks import run_pre_trade_risk_checks

        recon_path = PROJECT_ROOT / "data" / "broker" / "reconciliations" / f"{profile}_reconciliation.parquet"
        if not recon_path.exists():
            recon_path = recon_path.with_suffix(".csv")
        basket_df = (
            pd.read_parquet(recon_path) if recon_path.suffix == ".parquet"
            else pd.read_csv(recon_path)
        )

        # NAV from latest account summary.
        acct_dir = PROJECT_ROOT / "data" / "broker" / "account"
        files = sorted(acct_dir.glob(f"{profile}_account_summary_*.csv"))
        nav = 0.0
        if files:
            try:
                summary = pd.read_csv(files[-1])
                tag_col = "tag" if "tag" in summary.columns else summary.columns[0]
                val_col = "value" if "value" in summary.columns else summary.columns[1]
                hit = summary.loc[
                    summary[tag_col].astype(str).str.lower() == "netliquidation"
                ]
                if not hit.empty:
                    nav = float(str(hit.iloc[0][val_col]).replace(",", ""))
            except Exception as exc:
                log.warning("Could not parse NAV from account summary: %s", exc)

        prices_path = PROJECT_ROOT / "data" / "market" / "cleaned" / "prices" / "etf_prices_master.parquet"
        risk = run_pre_trade_risk_checks(
            basket_df=basket_df,
            nav=nav,
            prices_path=prices_path,
        )
        log.info(risk.report())
        leverage = check_leverage(profile, root=PROJECT_ROOT)
        log.info(leverage.report())
        if not (risk.ok and leverage.ok):
            log.error("✗ Pre-trade risk checks BLOCKED submission.")
            return {
                "ok": False,
                "action": "submit_orders",
                "blocked": True,
                "reasons": risk.blocking_reasons + leverage.blocking_reasons,
                "warnings": risk.warnings + leverage.warnings,
                "risk_details": {**risk.details, "leverage": leverage.details},
            }
    except Exception as exc:
        # If the risk gate itself fails to load, that's a *fail-open* risk —
        # block submission rather than send orders blind.
        log.exception("Risk gate raised — blocking submission as a precaution.")
        return {
            "ok": False,
            "action": "submit_orders",
            "blocked": True,
            "reasons": [f"Risk gate error: {type(exc).__name__}: {exc}"],
        }

    if dry_run:
        log.info("DRY RUN — skipping order submission.")
        return {"ok": True, "action": "submit_orders", "skipped": True, "reason": "dry_run"}

    try:
        from src.dashboard.services.order_service import submit_paper_orders
        result = submit_paper_orders(
            profile=profile,
            host=host,
            port=port,
            client_id=client_id,
            force=False,
            dry_run=False,
        )
        if result.get("ok"):
            log.info(
                "✓ Orders submitted. submitted=%s · path=%s",
                result.get("submitted"),
                result.get("submission_path"),
            )
        else:
            log.error("✗ Submit failed: %s", result.get("reason") or result.get("error"))
        return result
    except Exception as exc:
        log.exception("Submit orders error")
        return {"ok": False, "action": "submit_orders", "error": str(exc)}


# ── Main runner ───────────────────────────────────────────────────────────────

def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT_PAPER,
    client_id: int = DEFAULT_CLIENT_ID,
    profile: str = "paper",
    lookback: str = "5 D",
    dry_run: bool = False,
    force_rebalance: bool = False,
    skip_append: bool = False,
) -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = str(_today())

    log.info("=" * 60)
    log.info("DAILY RUNNER — %s", today)
    log.info("  Profile   : %s", profile)
    log.info("  Endpoint  : %s:%s (client %s)", host, port, client_id)
    log.info("  Dry run   : %s", dry_run)
    log.info("  Force rebalance : %s", force_rebalance)
    log.info("=" * 60)

    run_result: dict[str, Any] = {
        "run_id": run_id,
        "date": today,
        "profile": profile,
        "dry_run": dry_run,
        "steps": {},
    }

    # ── Step 1: Append daily bars ─────────────────────────────────────────────
    if not skip_append:
        append_result = step_append_daily(
            host=host, port=port, client_id=client_id,
            profile=profile, lookback=lookback,
        )
        run_result["steps"]["append_daily"] = append_result
        if not append_result.get("ok"):
            log.error("Append failed — aborting pipeline.")
            run_result["ok"] = False
            run_result["error"] = "append_daily failed"
            _write_run_log(run_id, run_result)
            return run_result

        # Step 1b/1c — fail-soft refreshes of tracked-but-untraded series
        # (indices, continuous futures). Errors here never block the ETF
        # trading pipeline; we just record them in the run log.
        run_result["steps"]["append_indices_daily"] = step_append_indices_daily(
            host=host, port=port, client_id=client_id + 2,
            profile=profile, lookback=lookback,
        )
        run_result["steps"]["append_futures_daily"] = step_append_futures_daily(
            host=host, port=port, client_id=client_id + 4,
            profile=profile, lookback=lookback,
        )
    else:
        log.info("Skipping append (--skip-append flag).")
        run_result["steps"]["append_daily"] = {"skipped": True}
        run_result["steps"]["append_indices_daily"] = {"skipped": True}
        run_result["steps"]["append_futures_daily"] = {"skipped": True}

    # ── Step 2: Generate targets ──────────────────────────────────────────────
    targets_result = step_generate_targets(profile=profile)
    run_result["steps"]["generate_targets"] = targets_result

    if not targets_result.get("ok"):
        log.error("Target generation failed — aborting.")
        run_result["ok"] = False
        run_result["error"] = "generate_targets failed"
        _write_run_log(run_id, run_result)
        return run_result

    # ── Rebalance decision ────────────────────────────────────────────────────
    rb_cfg = _load_rebalance_config()
    log.info(
        "Rebalance cadence: %s (drift_threshold=%.3f · composition_threshold=%.3f · cooldown=%sd)",
        rb_cfg["cadence"],
        float(rb_cfg["drift_threshold"]),
        float(rb_cfg["composition_threshold"]),
        rb_cfg["cooldown_days"],
    )
    targets_df = _load_latest_targets()
    outside = _holdings_outside_model(
        targets_df, profile=profile, host=host, port=port, client_id=client_id + 10,
    )
    should_rebalance, rebalance_diag = _needs_rebalance(
        targets_df, profile=profile, rb_cfg=rb_cfg, force=force_rebalance, outside=outside,
    )
    run_result["should_rebalance"] = should_rebalance
    run_result["rebalance_decision"] = rebalance_diag

    if not should_rebalance:
        log.info("No rebalance needed today. Done.")
        run_result["ok"] = True
        _write_run_log(run_id, run_result)
        return run_result

    # ── Step 3: Build basket ──────────────────────────────────────────────────
    basket_result = step_build_basket(
        host=host, port=port, client_id=client_id + 10,
        profile=profile, dry_run=dry_run,
    )
    run_result["steps"]["build_basket"] = basket_result

    if not basket_result.get("ok"):
        log.error("Basket build failed — aborting order submission.")
        run_result["ok"] = False
        run_result["error"] = "build_basket failed"
        _write_run_log(run_id, run_result)
        return run_result

    # ── Step 4: Submit orders ─────────────────────────────────────────────────
    # Orders always go out on the fixed "orders" clientId so they can be
    # cancelled / modified later from any tool that uses that role.
    submit_result = step_submit_orders(
        host=host, port=port, client_id=client_id_for("orders", profile),
        profile=profile, dry_run=dry_run,
    )
    run_result["steps"]["submit_orders"] = submit_result

    overall_ok = submit_result.get("ok", False)
    run_result["ok"] = overall_ok

    # ── Persist last-rebalance state so the next drift gate can read it.
    # Only do this on a real submission (no dry-run, no risk-blocked path,
    # and only when the drift cadence is in use). The state file is what
    # cooldown + composition checks compare against.
    submission_actually_happened = (
        overall_ok
        and not dry_run
        and not submit_result.get("skipped")
        and not submit_result.get("blocked")
        and submit_result.get("submitted")
    )
    if submission_actually_happened and rb_cfg.get("cadence") == "drift":
        try:
            recon = _load_reconciliation(profile)
            if recon is not None and not recon.empty:
                target_weights, _ = _weights_from_reconciliation(recon)
                _save_last_rebalance_state(
                    PROJECT_ROOT / rb_cfg["state_path"],
                    submitted_at=_today(),
                    target_weights=target_weights,
                )
        except Exception as exc:
            # State write is best-effort — a failure here is not worth
            # failing the whole run for.
            log.warning("Could not persist last_rebalance state: %s", exc)

    log.info("=" * 60)
    log.info("DAILY RUNNER COMPLETE — %s", "✓ OK" if overall_ok else "✗ FAILED")
    log.info("=" * 60)

    _write_run_log(run_id, run_result)
    return run_result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Daily auto-trade runner")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "IBKR API port. Defaults to IBKR_PORT_PAPER env var (or 4002) "
            "for paper, IBKR_PORT_LIVE (or 4001) for live."
        ),
    )
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID, dest="client_id")
    parser.add_argument("--profile", default="paper", choices=["paper", "live"])
    parser.add_argument("--lookback", default="5 D", help="Lookback for daily bar append")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Run everything except order submission")
    parser.add_argument("--force-rebalance", action="store_true", dest="force_rebalance",
                        help="Submit orders even if no new rebalance date detected")
    parser.add_argument("--skip-append", action="store_true", dest="skip_append",
                        help="Skip IBKR data append (use existing data)")
    parser.add_argument(
        "--skip-if-ran-today", action="store_true", dest="skip_if_ran_today",
        help=(
            "Idempotency guard for the LaunchAgent's RunAtLoad fire: exit "
            "immediately if today is a weekend, or if today's run log already "
            "exists with ok=True. Designed for catch-up on login when the "
            "scheduled fire was missed (Mac off / asleep)."
        ),
    )
    parser.add_argument("--json-out", action="store_true", help="Print result JSON to stdout")
    args = parser.parse_args()

    # ── Catch-up guard ─────────────────────────────────────────────────
    # Run when EITHER:
    #   • the scheduler fires us at 16:32 on a weekday, OR
    #   • the user logs in and the scheduled fire was missed.
    # Skip when the user logs in on a weekend, or already after today's
    # run succeeded (so RunAtLoad is harmless on every subsequent login).
    if args.skip_if_ran_today:
        if _today().weekday() >= 5:
            log.info("Catch-up: today is a weekend → skip.")
            sys.exit(0)
        prior = _already_ran_today_successfully()
        if prior is not None:
            log.info("Catch-up: today's run already succeeded (%s) → skip.", prior.name)
            sys.exit(0)
        log.info("Catch-up: no successful run for %s yet → executing now.", _today())

    # Resolve port: CLI > env > profile default.
    resolved_port = args.port
    if resolved_port is None:
        resolved_port = (
            DEFAULT_PORT_PAPER if args.profile == "paper" else DEFAULT_PORT_LIVE
        )

    result = run(
        host=args.host,
        port=resolved_port,
        client_id=args.client_id,
        profile=args.profile,
        lookback=args.lookback,
        dry_run=args.dry_run,
        force_rebalance=args.force_rebalance,
        skip_append=args.skip_append,
    )

    if args.json_out:
        print(json.dumps(result, indent=2, default=str))

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
