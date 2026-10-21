#!/usr/bin/env python3
"""
daily_runner.py — Daily auto-trade pipeline.

Run this at 15:35 ET every trading day (bin/tradeit schedule install sets
that up), before the closing auctions stop taking orders. It handles the full
cycle automatically:

  0. Reconcile the orders earlier runs sent against IBKR's positions
     (src/runtime/end_of_day_reconcile.py): a material break arms the kill
     switch, and a reconcile that couldn't run blocks this run's orders
  1. Append IBKR daily bars to the master price file, up to the last
     completed session (before the close, yesterday's: today's bar is partial)
  2. Regenerate targets from those closes
  3. Detect if today is a rebalance date (end-of-month or new signal; drift
     of the account's positions now from today's targets; or shares held
     outside the model, which the basket sells)
  4. If rebalance needed: build basket (diff vs current positions)
  5. Submit paper orders to IBKR, sells first: market-on-close, filled at
     today's close, when sent by 15:45 ET; market-on-open for the next session
     after that or when the market is closed (src/broker/ibkr/auction.py)
  6. Write structured log to data/logs/runtime/

On a weekend or a market holiday (src/common/clock.py) a real run stops at
once and logs why; a dry run still refreshes bars and targets.

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
from datetime import datetime, date, timezone
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
from src.common import clock

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


def _market_closed_reason(today: date) -> str | None:
    """Why NYSE has no session on ``today`` (a weekend or a market holiday), or
    None when it trades. A real run skips closed days: there is no close to
    trade at, and orders sent then would go market-on-open for the next session
    without anyone asking for them."""
    if clock.is_us_business_day(today):
        return None
    return "a weekend" if today.weekday() >= 5 else "a market holiday"


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


def _load_execution_config() -> dict[str, Any]:
    """The submit switches in config/execution.yaml (src/production/execution_config.py)."""
    from src.production import execution_config

    return execution_config.load(PROJECT_ROOT / "config" / "execution.yaml")


def _submit_disabled(exec_cfg: dict[str, Any], profile: str) -> str | None:
    """Why config/execution.yaml stops this run sending orders, or None when it allows them."""
    from src.production import execution_config

    return execution_config.submit_disabled(exec_cfg, profile)


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


def _nav_from_summary(profile: str) -> float | None:
    """NetLiquidation from the latest saved account summary, if any."""
    import pandas as pd

    files = sorted((PROJECT_ROOT / "data" / "broker" / "account").glob(f"{profile}_account_summary_*.csv"))
    if not files:
        return None
    try:
        summary = pd.read_csv(files[-1])
        tag_col = "tag" if "tag" in summary.columns else summary.columns[0]
        val_col = "value" if "value" in summary.columns else summary.columns[1]
        hit = summary.loc[summary[tag_col].astype(str).str.lower() == "netliquidation"]
        if hit.empty:
            return None
        nav = float(str(hit.iloc[0][val_col]).replace(",", ""))
        return nav if nav > 0 else None
    except Exception as exc:
        log.warning("Could not parse NAV from %s: %s", files[-1].name, exc)
        return None


def _current_positions(
    profile: str, host: str, port: int, client_id: int,
) -> tuple["pd.DataFrame | None", float | None, str]:
    """Every position in the account, NAV, and where they came from: IBKR now,
    else the last positions snapshot and account summary. The snapshot is
    written when a basket is built, before its orders fill, so it is only the
    fallback. Read once per run, for the drift gate and the outside-model check."""
    import pandas as pd

    try:
        from src.broker.ibkr.client import IBKRClient, IBKRConnectionConfig
        from src.production.runtime.build_paper_basket import fetch_positions_df

        client = IBKRClient(
            IBKRConnectionConfig(host=host, port=port, client_id=client_id, expected_profile=profile)
        )
        client.connect()
        try:
            return fetch_positions_df(client), client.net_liquidation(), "ibkr"
        finally:
            client.disconnect()
    except Exception as exc:
        log.warning("Could not read positions from IBKR (%s: %s); using the last snapshot.", type(exc).__name__, exc)
    path = PROJECT_ROOT / "data" / "broker" / "positions" / f"{profile}_positions_snapshot.csv"
    if path.exists():
        return pd.read_csv(path), _nav_from_summary(profile), "snapshot"
    return None, None, "none"


def _latest_targets(targets_df: "pd.DataFrame") -> "pd.DataFrame":
    """Today's targets: the latest rebalance date's rows, in the model universe."""
    from src.production.generate_targets import model_universe

    latest = targets_df.loc[targets_df["rebalance_date"] == targets_df["rebalance_date"].max()]
    return latest.loc[latest["symbol"].astype(str).str.upper().isin(model_universe())]


def _current_weights(positions: "pd.DataFrame", nav: float, fallback_prices: dict[str, float]) -> dict[str, float]:
    """Each share holding's weight of NAV: shares times IBKR's mark, else the
    symbol's last close (``fallback_prices``). Only shares: the basket trades
    shares, and a future or option it can't sell would otherwise show as drift
    on every run. A holding with no price is left out, with a warning."""
    from src.production.runtime.build_paper_basket import SHARES

    weights: dict[str, float] = {}
    for row in positions.to_dict("records"):
        qty = float(row.get("current_shares") or 0.0)
        sec_type = row.get("sec_type") if isinstance(row.get("sec_type"), str) else ""
        if not qty or sec_type not in SHARES:
            continue
        symbol = str(row["symbol"]).upper().strip()
        try:
            mark = float(row.get("market_price"))
        except (TypeError, ValueError):
            mark = float("nan")
        price = mark if mark == mark and mark > 0 else fallback_prices.get(symbol)
        if not price:
            log.warning("No price for %s; it is left out of the drift check.", symbol)
            continue
        weights[symbol] = weights.get(symbol, 0.0) + qty * price / nav
    return weights


def _holdings_outside_model(
    targets_df: "pd.DataFrame | None",
    positions: "pd.DataFrame | None",
    source: str,
) -> dict[str, Any]:
    """Shares the account holds that the latest targets don't include (the
    basket sells them whole) and holdings it leaves alone (futures, options).
    JSON-safe, for the run log."""
    from src.production.runtime.build_paper_basket import holdings_outside_model

    out: dict[str, Any] = {"sell": [], "kept": [], "warnings": [], "positions_from": source}
    if targets_df is None or targets_df.empty or positions is None:
        return out
    latest = _latest_targets(targets_df)
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
    *,
    targets_df: "pd.DataFrame | None" = None,
    positions: "pd.DataFrame | None" = None,
    nav: float | None = None,
    source: str = "none",
) -> tuple[bool, dict[str, Any]]:
    """Drift-band cadence: daily check, fire on holdings outside the model,
    composition or drift, comparing today's targets with the account's
    weights now (``positions`` over ``nav``, from IBKR or the last snapshot).
    The previous run's reconciliation file is not used: it holds that run's
    targets and the shares held before its orders filled, so a composition
    change would never show.

    Returns ``(should_rebalance, diagnostics)`` where ``diagnostics`` is
    a JSON-safe dict suitable for the run log.
    """
    from src.production.runtime.build_paper_basket import load_last_closes
    from src.runtime.auto_rebalance import decide_rebalance_with_drift

    if targets_df is None or targets_df.empty:
        log.info("No targets → will rebalance (cold start).")
        return True, {"reason": "no targets", "cold_start": True, "positions_from": source}
    if positions is None or nav is None or nav <= 0:
        log.info("No positions or NAV to compare with today's targets → will rebalance (cold start).")
        return True, {"reason": "no positions or NAV to compare with today's targets", "cold_start": True,
                      "positions_from": source}

    latest = _latest_targets(targets_df)
    target_weights = {
        str(row["symbol"]).upper(): float(row["target_weight"])
        for _, row in latest.iterrows()
        if float(row.get("target_weight", 0.0)) > 0
    }
    closes = load_last_closes(PROJECT_ROOT / "data" / "market" / "cleaned" / "prices" / "etf_prices_master.parquet")
    if "close" in latest.columns:
        closes.update({str(s).upper(): float(c) for s, c in zip(latest["symbol"], latest["close"]) if float(c) > 0})
    current_weights = _current_weights(positions, nav, closes)

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
        "positions_from": source,
        "nav": nav,
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
    *,
    positions: "pd.DataFrame | None" = None,
    nav: float | None = None,
    source: str = "none",
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
        decided, diag = _needs_rebalance_drift(
            profile=profile, rb_cfg=rb_cfg, force=force, outside_model=sell,
            targets_df=targets_df, positions=positions, nav=nav, source=source,
        )
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


def _halted_reason() -> str | None:
    """Why the kill switch stops trading, or None while it is clear."""
    from src.execution.kill_switch import KillSwitchCorrupted, read_kill_switch_state

    try:
        state = read_kill_switch_state()
    except KillSwitchCorrupted as exc:
        return f"kill switch state unreadable ({exc})"
    return (state.reason or "no reason given") if state.armed else None


def _to_store(what: str, write: Any) -> None:
    """Copy part of the run to the Postgres store (src/storage) when it is on.
    Best effort: a store that is off or down never touches the run."""
    try:
        from src.storage.record import best_effort
    except Exception as exc:  # noqa: BLE001
        log.debug("store not available: %s", exc)
        return
    best_effort(what, write)


def _write_run_log(run_id: str, result: dict[str, Any]) -> Path:
    # A halted fund needs a person, so a real run reports an armed kill switch as
    # a failure whatever else it did: systemd's OnFailure alert then fires every
    # day until someone resumes. On 2026-10-19 the switch armed itself and the
    # fund sat halted for two days with nobody told; a day with nothing to
    # rebalance would have looked like a good run.
    if not result.get("dry_run") and not result.get("skipped"):
        halted = _halted_reason()
        if halted:
            result.update(ok=False, halted=halted)
            log.error("The kill switch is armed (%s): nothing trades until bin/tradeit resume.", halted)
    run_dir = PROJECT_ROOT / "artifacts" / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / f"daily_run_{run_id}.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)
    log.info("Run log saved: %s", out)

    def write(conn: Any) -> None:
        from src.storage.record import record_run

        record_run(conn, {**result, "run_id": run_id}, ended_at=datetime.now(timezone.utc))

    _to_store("the run log", write)
    return out


def _already_ran_today_successfully() -> Optional[Path]:
    """Return the path of today's successful run log, if one exists.

    Used by the catch-up flag (``--skip-if-ran-today``) to make the
    LaunchAgent's RunAtLoad fire idempotent: log in any time after a
    successful scheduled run and the runner exits without doing anything,
    but log in after a *missed* one (Mac was off, asleep, or you'd
    user-switched away too long) and it executes the catch-up. A catch-up
    after 15:45 ET sends its orders market-on-open, for the next session.

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

def step_reconcile(host: str, port: int, profile: str) -> dict[str, Any]:
    """Reconcile the orders earlier runs sent against IBKR's positions now
    (src/runtime/end_of_day_reconcile.py). A material break arms the kill
    switch, which then blocks this run's orders. A reconcile that couldn't run
    (gateway unreachable, book unreadable) blocks them too, in step_submit_orders:
    the book is unverified."""
    log.info("── Step 0: Reconcile earlier orders against IBKR ───────────")
    try:
        from src.broker.ibkr.settings import make_client
        from src.runtime.end_of_day_reconcile import reconcile_end_of_day

        res = reconcile_end_of_day(
            profile=profile,
            client=make_client(profile, "reconcile", host=host, port=port, readonly=True),
            root=PROJECT_ROOT,
        )
    except Exception as exc:
        log.exception("Reconcile raised")
        return {"ok": False, "action": "reconcile", "error": f"{type(exc).__name__}: {exc}"}
    out: dict[str, Any] = {
        "ok": res.ok,
        "action": "reconcile",
        "material_mismatch": res.material_mismatch,
        "diffs": res.diffs,
        "report_path": res.report_path,
    }
    if res.skipped:
        out.update(skipped=True, reason=res.skipped)
        log.info("Reconcile skipped: %s", res.skipped)
    elif res.error:
        out["error"] = res.error
        log.error("✗ Reconcile failed: %s (this run sends no orders)", res.error)
    elif res.material_mismatch:
        log.error("✗ Reconcile: %s breaks against IBKR, kill switch armed; see %s", len(res.diffs), res.report_path)
    elif res.diffs:
        log.warning("Reconcile: %s small breaks against IBKR, under the threshold; see %s", len(res.diffs), res.report_path)
    else:
        log.info("✓ Reconcile: the book matches IBKR to the share (%s orders counted).", len(res.orders))
    return out


def _record_submission(profile: str, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Add the orders just sent to the expected book, for the next run's
    reconcile: the positions snapshot the basket was built on plus the
    submission log (symbol, action, quantity, IBKR ids, status)."""
    import pandas as pd
    from src.runtime.end_of_day_reconcile import record_submission

    try:
        positions = pd.read_csv(PROJECT_ROOT / "data" / "broker" / "positions" / f"{profile}_positions_snapshot.csv")
        orders = pd.read_csv(result["submission_path"])
        path = record_submission(
            profile,
            run_id=run_id,
            positions=positions,
            orders=orders,
            execution=(result.get("result") or {}).get("execution"),
            root=PROJECT_ROOT,
        )
        log.info("Recorded the submission in the expected book: %s", path)
        return {"ok": True, "book": str(path)}
    except Exception as exc:
        # The next reconcile then sees these fills as breaks and arms the switch:
        # the safe side, and the run log says why.
        log.exception("Could not record the submission in the expected book")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _store_orders(conn: Any, profile: str, submission_path: Any) -> None:
    """The submission log just written, as the store's orders rows."""
    from src.storage.record import read_submission, record_submission, submission_batch

    batch = submission_batch(submission_path)
    if batch:
        record_submission(conn, profile, batch, read_submission(submission_path))


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
            "✓ Appended %s new rows for %s symbols. Latest date: %s (bars through %s, the last completed session)",
            result.get("new_rows_added_to_master", 0),
            result.get("symbols_with_data", 0),
            result.get("latest_date"),
            result.get("completed_through"),
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
            "✓ Targets written. Rebalance dates: %s. Latest: %s (prices through %s; signals on %s).",
            result.get("rebalance_dates", 0),
            result.get("latest_rebalance_date"),
            result.get("prices_through"),
            (result.get("signal_prices") or "price").replace("_", "-"),
        )
        if result.get("signal_prices_note"):
            log.warning("Signals: %s", result["signal_prices_note"])
    else:
        log.error("✗ Target generation failed: %s", result.get("error"))

    return result


def step_update_total_return(host: str, port: int, client_id: int) -> dict[str, Any]:
    """After the run: refresh the model's dividend-adjusted closes, which the
    next run's signals read (src/production/pipeline/append_total_return.py).
    Runs after the orders, so it never delays the market-on-close cutoff, and a
    failure here only leaves the previous closes in place."""
    log.info("── After the run: total-return closes ───────────────────────")
    try:
        from src.production.pipeline.append_total_return import append_total_return

        result = append_total_return(host=host, port=port, client_id=client_id)
    except Exception as exc:  # noqa: BLE001 — never fails the run
        log.exception("Total-return closes: refresh raised")
        return {"ok": False, "action": "total_return_closes", "error": f"{type(exc).__name__}: {exc}"}
    if result.get("ok"):
        log.info("✓ Total-return closes refreshed for %d ETFs.", len(result.get("refreshed") or []))
    else:
        log.warning("Total-return closes: %s", result.get("error") or result.get("errors"))
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
    *,
    run_id: str | None = None,
    reconciled: bool = True,
) -> dict[str, Any]:
    """``reconciled`` is whether step_reconcile passed (or had nothing to do);
    a run whose reconcile failed sends nothing. ``run_id`` labels the
    submission in the expected book."""
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

        # NAV from the latest account summary; a missing one blocks (NAV 0).
        nav = _nav_from_summary(profile) or 0.0

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
        from src.broker.ibkr.auction import execution_for

        log.info(
            "DRY RUN — skipping order submission. Sent now, they would go %s.",
            execution_for(basket_df["symbol"]).summary(),
        )
        return {"ok": True, "action": "submit_orders", "skipped": True, "reason": "dry_run"}

    if not reconciled:
        log.error("✗ The reconcile of earlier orders did not pass: not trading on an unverified book.")
        return {
            "ok": False,
            "action": "submit_orders",
            "blocked": True,
            "reasons": ["the end-of-day reconcile did not pass (see the reconcile step): the book is unverified"],
        }

    try:
        from src.production.runtime.order_service import submit_paper_orders
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
                "✓ Orders submitted, %s. submitted=%s · path=%s",
                ((result.get("result") or {}).get("execution") or {}).get("summary"),
                result.get("submitted"),
                result.get("submission_path"),
            )
            if result.get("submitted"):
                result["recorded"] = _record_submission(
                    profile, run_id or datetime.now().strftime("%Y%m%d_%H%M%S"), result
                )
                _to_store("the orders sent", lambda conn: _store_orders(conn, profile, result["submission_path"]))
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

    # ── Closed days ───────────────────────────────────────────────────────────
    closed = _market_closed_reason(_today())
    if closed and not dry_run:
        log.info("NYSE is closed today (%s): no close to trade at, so the run is skipped.", closed)
        run_result.update(ok=True, skipped=True, reason=f"NYSE closed: {closed}")
        _write_run_log(run_id, run_result)
        return run_result
    if clock.is_early_close(_today()):
        log.info(
            "Early close today (13:00 ET). A run after it sends its orders market-on-open "
            "for the next session; that is by design, not a fault."
        )

    # ── May this run send orders? config/execution.yaml decides ──────────────
    # A run it doesn't allow is a dry run: every step and every check, nothing
    # sent, and the run log says which setting stopped it.
    if not dry_run:
        off = _submit_disabled(_load_execution_config(), profile)
        if off:
            log.warning("Orders are switched off: %s. This run is a dry run.", off)
            dry_run = True
            run_result.update(dry_run=True, submit_disabled=off)

    # ── Step 0: Reconcile the orders earlier runs sent ────────────────────────
    reconcile_result = step_reconcile(host=host, port=port, profile=profile)
    run_result["steps"]["reconcile"] = reconcile_result

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
    positions, nav, source = _current_positions(profile, host, port, client_id + 10)
    outside = _holdings_outside_model(targets_df, positions, source)
    should_rebalance, rebalance_diag = _needs_rebalance(
        targets_df, profile=profile, rb_cfg=rb_cfg, force=force_rebalance, outside=outside,
        positions=positions, nav=nav, source=source,
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
        run_id=run_id, reconciled=bool(reconcile_result.get("ok")),
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
            "immediately if NYSE is closed today (a weekend or a holiday), or "
            "if today's run log already exists with ok=True. Designed for "
            "catch-up on login when the scheduled fire was missed (Mac off / asleep)."
        ),
    )
    parser.add_argument("--json-out", action="store_true", help="Print result JSON to stdout")
    args = parser.parse_args()

    # ── Catch-up guard ─────────────────────────────────────────────────
    # Run when EITHER:
    #   • the scheduler fires us on a weekday (15:35 ET), OR
    #   • the user logs in and the scheduled fire was missed.
    # Skip when the user logs in on a day NYSE is closed, or already after
    # today's run succeeded (so RunAtLoad is harmless on every subsequent login).
    if args.skip_if_ran_today:
        closed = _market_closed_reason(_today())
        if closed:
            log.info("Catch-up: NYSE is closed today (%s) → skip.", closed)
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

    # The next run's signals; after this run's orders, and only when it read IBKR.
    if not result.get("skipped") and not args.skip_append:
        result.setdefault("steps", {})["total_return_closes"] = step_update_total_return(
            host=args.host, port=resolved_port, client_id=args.client_id + 6,
        )
        if result.get("run_id"):
            _write_run_log(result["run_id"], result)

    if args.json_out:
        print(json.dumps(result, indent=2, default=str))

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
