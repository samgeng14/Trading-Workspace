"""
risk_checks.py — Pre-trade risk gate.

Runs every check that has to pass before orders go to IBKR. Composed of:
  * kill switch
  * data validators (master prices)
  * exposure limits (basket-level)
  * basic sanity (basket non-empty, prices recent enough, NAV > 0)

Returns a single ``PreTradeRiskResult`` so callers can present results in a
uniform way. The daily runner calls this from ``step_submit_orders`` and
aborts on failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.execution.kill_switch import (
    KillSwitchTripped,
    read_kill_switch_state,
    require_kill_switch_clear,
)
from src.portfolio.risk.exposure_limits import (
    DEFAULT_EXPOSURE_LIMITS,
    ExposureCheckResult,
    ExposureLimits,
    check_exposure_limits,
)


@dataclass
class PreTradeRiskResult:
    ok: bool
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def report(self) -> str:
        out = [f"Pre-trade risk: {'PASS' if self.ok else 'BLOCK'}"]
        for r in self.blocking_reasons:
            out.append(f"  ✗ {r}")
        for w in self.warnings:
            out.append(f"  ⚠ {w}")
        return "\n".join(out)


def run_pre_trade_risk_checks(
    *,
    basket_df: pd.DataFrame,
    nav: float,
    prices_path: str | Path | None = None,
    limits: ExposureLimits | None = None,
    as_of: date | None = None,
) -> PreTradeRiskResult:
    """Top-level pre-trade gate.

    ``basket_df`` is the reconciliation DataFrame (the workspace's standard
    output of build_paper_basket): one row per symbol with columns
    ``target_weight``, ``estimated_trade_dollars``, ``side``. ``as_of`` is the
    session the price file should reach (default: the last completed one now).
    """
    res = PreTradeRiskResult(ok=True)
    limits = limits or DEFAULT_EXPOSURE_LIMITS

    # ── 1. Kill switch ─────────────────────────────────────────────────────
    try:
        require_kill_switch_clear()
        ks = read_kill_switch_state()
        res.details["kill_switch"] = ks.to_dict()
    except KillSwitchTripped as exc:
        res.ok = False
        res.blocking_reasons.append(str(exc))
        res.details["kill_switch"] = read_kill_switch_state().to_dict()
        # Even if the switch is armed we run the rest so the operator can
        # see the full picture in one report — but we do flag ok=False.

    # ── 2. NAV sanity ──────────────────────────────────────────────────────
    if nav is None or nav <= 0:
        res.ok = False
        res.blocking_reasons.append(f"NAV is non-positive: {nav!r}")
    res.details["nav"] = float(nav) if nav is not None else None

    # ── 3. Basket sanity ───────────────────────────────────────────────────
    if basket_df is None or basket_df.empty:
        res.ok = False
        res.blocking_reasons.append("Basket is empty")
        return res

    cols = {c.lower() for c in basket_df.columns}
    if "target_weight" not in cols or "symbol" not in cols:
        res.ok = False
        res.blocking_reasons.append(
            f"Basket missing required columns. Got: {sorted(cols)}"
        )
        return res

    # Lower-case once for safety.
    df = basket_df.copy()
    df.columns = [c.lower() for c in df.columns]

    # ── 4. Master price file (optional but recommended) ────────────────────
    if prices_path is not None:
        try:
            from src.data.cleaning.validators import validate_master_prices_file
            from src.production.generate_targets import model_universe

            # a model ETF whose last bar lags the last completed session blocks
            v = validate_master_prices_file(prices_path, model_symbols=model_universe(), as_of=as_of)
            res.details["price_validation"] = {
                "ok": v.ok,
                "errors": v.errors,
                "warnings": v.warnings,
                "summary": v.summary,
            }
            if not v.ok or v.errors:
                res.ok = False
                res.blocking_reasons.extend(
                    f"prices: {e}" for e in (v.errors or ["price validation failed"])
                )
            res.warnings.extend(f"prices: {w}" for w in v.warnings)
        except Exception as exc:
            # Fail closed: prices that couldn't be validated are not a pass.
            res.ok = False
            res.blocking_reasons.append(
                f"prices: validation could not run: {type(exc).__name__}: {exc}"
            )

    # ── 5. Exposure limits ────────────────────────────────────────────────
    weights_series = (
        df.set_index("symbol")["target_weight"].astype(float).rename(None)
    )
    trades_series = None
    if "estimated_trade_dollars" in df.columns:
        trades_series = (
            df.set_index("symbol")["estimated_trade_dollars"].astype(float).abs()
        )

    cash_after_pct = max(0.0, 1.0 - float(weights_series.sum()))
    expo: ExposureCheckResult = check_exposure_limits(
        weights_series,
        nav=nav,
        trades_dollars=trades_series,
        cash_after_pct=cash_after_pct,
        limits=limits,
    )
    res.details["exposure"] = {
        "summary": expo.summary,
        "breaches": [
            {"name": b.name, "detail": b.detail, "severity": b.severity}
            for b in expo.breaches
        ],
    }
    for b in expo.breaches:
        if b.severity == "fail":
            res.ok = False
            res.blocking_reasons.append(f"exposure: {b.name}: {b.detail}")
        else:
            res.warnings.append(f"exposure: {b.name}: {b.detail}")

    return res
