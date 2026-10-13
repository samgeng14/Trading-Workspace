"""
auto_rebalance.py — Decide if today is a rebalance day.

A small, intentionally pure module: takes the latest targets DataFrame
and a date, returns ``True`` / ``False`` plus a reason string.  No I/O.
The caller is responsible for loading the targets and acting on the
decision.

Two cadence policies live here:

  * ``decide_rebalance`` — monthly cadence (legacy). Rebalance when the
    calendar month rolls over or there is no prior signal. This mirrors
    the original ``production/daily_runner._needs_rebalance`` and is
    kept for backward compatibility / paper safety.
  * ``decide_rebalance_with_drift`` — daily cadence with a drift band.
    Rebalance when the account holds shares outside the model (the
    basket sells them), when actual weights drift past a threshold from
    target, when the materially-weighted basket composition changes, or
    as a cold start. Includes a global cooldown to suppress same-day churn.

Both are pure: tests hammer them with synthetic inputs without touching
disk or the broker. The daily_runner picks which to call from config.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True, slots=True)
class RebalanceDecision:
    rebalance: bool
    reason: str
    latest_signal_date: Optional[date]


def _last_calendar_day(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def is_month_end_window(d: date, *, days: int = 5) -> bool:
    """True if ``d`` is in the last ``days`` calendar days of its month."""
    if not isinstance(d, date):
        raise TypeError(f"d must be a datetime.date, got {type(d).__name__}")
    if not isinstance(days, int) or days < 1:
        raise ValueError(f"days must be a positive int, got {days!r}")
    if days >= 31:
        # Any day of any month is in the window.
        return True
    return d.day >= _last_calendar_day(d) - (days - 1)


def decide_rebalance(
    latest_signal_date: Optional[date],
    today: date,
    *,
    force: bool = False,
) -> RebalanceDecision:
    """Decide whether to rebalance.

    Rules (any one triggers rebalance):
      * ``force=True``
      * No signal date at all (cold start)
      * Signal date is in a strictly earlier month (missed cycle)
      * Signal date is in the future (clock skew or replay)

    Otherwise we skip — the position should already be on target.
    """
    if not isinstance(today, date):
        raise TypeError(f"today must be a datetime.date, got {type(today).__name__}")
    if latest_signal_date is not None and not isinstance(latest_signal_date, date):
        raise TypeError(
            f"latest_signal_date must be date or None, got {type(latest_signal_date).__name__}"
        )

    if force:
        return RebalanceDecision(True, "force flag set", latest_signal_date)
    if latest_signal_date is None:
        return RebalanceDecision(True, "no prior signal — cold start", None)
    if latest_signal_date > today:
        # Future date is a sign of clock skew / replay attack / corrupted
        # data. Refuse to skip rebalance silently — surface the anomaly.
        return RebalanceDecision(
            True,
            f"signal {latest_signal_date} is AFTER today {today} — clock anomaly, "
            f"forcing rebalance to refresh",
            latest_signal_date,
        )
    if (latest_signal_date.year, latest_signal_date.month) < (today.year, today.month):
        return RebalanceDecision(
            True,
            f"signal {latest_signal_date} is before current month "
            f"{today.year}-{today.month:02d}",
            latest_signal_date,
        )
    return RebalanceDecision(
        False,
        f"signal {latest_signal_date} already current — skip",
        latest_signal_date,
    )


@dataclass(frozen=True, slots=True)
class DriftDecision:
    """Outcome of the daily drift-band rebalance gate.

    Fields are intentionally rich so the caller can log *why* a rebalance
    fired (or didn't) without re-running the math.
    """

    rebalance: bool
    reason: str
    max_drift: float
    drift_per_symbol: Mapping[str, float]
    composition_changed: bool
    in_cooldown: bool
    cooldown_remaining_days: int
    outside_model: tuple[str, ...] = ()


def outside_model_reason(symbols: Sequence[str]) -> str:
    return f"holdings outside the model: {', '.join(symbols)}"


def _materially_weighted_set(
    weights: Mapping[str, float], min_weight: float
) -> frozenset[str]:
    """Symbols whose target weight is at or above the materiality cutoff."""
    if min_weight < 0:
        raise ValueError(f"min_weight must be ≥ 0, got {min_weight!r}")
    return frozenset(s for s, w in weights.items() if w >= min_weight)


def decide_rebalance_with_drift(
    *,
    target_weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    today: date,
    last_submitted_at: Optional[date] = None,
    last_basket: Optional[Mapping[str, float]] = None,
    drift_threshold: float = 0.05,
    composition_threshold: float = 0.05,
    cooldown_days: int = 3,
    force: bool = False,
    outside_model: Sequence[str] = (),
) -> DriftDecision:
    """Decide whether to rebalance today using a drift band.

    Triggers (any one fires the rebalance, evaluated in this order):

      1. ``force=True`` — operator override.
      2. Cold start — no ``last_basket`` recorded yet.
      3. Holdings outside the model — ``outside_model`` names shares the
         account holds that the targets don't include. The basket sells
         them whole, so the runner rebalances while any are left, however
         small.
      4. Composition change — the set of names with target weight at or
         above ``composition_threshold`` differs from the same set in
         ``last_basket``. Catches regime shifts (e.g. a new ETF entering
         the materially-held basket) regardless of weight magnitude.
      5. Drift band — ``max_symbol |target − current| > drift_threshold``.

    A global cooldown blocks triggers (3), (4) and (5) — but never (1) or
    (2) — when ``today - last_submitted_at < cooldown_days``. This is the
    fence against same-day churn on volatile days, and against selling a
    holding again before the last run's sell has filled (IBKR's positions
    don't show orders still working); it deliberately does *not* gate cold
    starts or operator forces.

    All thresholds are in fractional weight units (0.05 = 5%). All
    weights should be on the same NAV basis; the helper does not
    re-normalize.

    Returns a ``DriftDecision`` with the full diagnostic payload so
    callers can log the *why* without recomputing.
    """
    if not isinstance(today, date):
        raise TypeError(f"today must be a datetime.date, got {type(today).__name__}")
    if last_submitted_at is not None and not isinstance(last_submitted_at, date):
        raise TypeError(
            f"last_submitted_at must be date or None, "
            f"got {type(last_submitted_at).__name__}"
        )
    if drift_threshold < 0:
        raise ValueError(f"drift_threshold must be ≥ 0, got {drift_threshold!r}")
    if composition_threshold < 0:
        raise ValueError(
            f"composition_threshold must be ≥ 0, got {composition_threshold!r}"
        )
    if cooldown_days < 0:
        raise ValueError(f"cooldown_days must be ≥ 0, got {cooldown_days!r}")

    # ── Per-symbol drift on the union of target ∪ current names ──
    # Symbols missing from one side are treated as 0% (a name fully sold
    # off but still in the target list shows full target_weight as drift).
    universe = set(target_weights) | set(current_weights)
    drift_per_symbol: dict[str, float] = {
        s: abs(float(target_weights.get(s, 0.0)) - float(current_weights.get(s, 0.0)))
        for s in universe
    }
    max_drift = max(drift_per_symbol.values(), default=0.0)

    # ── Composition diff (only meaningful if we have a prior basket) ──
    composition_changed = False
    if last_basket is not None:
        prev_set = _materially_weighted_set(last_basket, composition_threshold)
        curr_set = _materially_weighted_set(target_weights, composition_threshold)
        composition_changed = prev_set != curr_set

    # ── Cooldown bookkeeping ──
    if last_submitted_at is None or cooldown_days == 0:
        in_cooldown = False
        cooldown_remaining = 0
    else:
        days_since = (today - last_submitted_at).days
        if days_since < 0:
            # Future last-submission date is a clock-skew anomaly. Don't
            # silently apply a cooldown; treat it as out-of-cooldown so
            # the rest of the gate can fire (or skip) on its own merits.
            in_cooldown = False
            cooldown_remaining = 0
        else:
            in_cooldown = days_since < cooldown_days
            cooldown_remaining = max(0, cooldown_days - days_since)

    def _decision(rebalance: bool, reason: str) -> DriftDecision:
        return DriftDecision(
            rebalance=rebalance,
            reason=reason,
            max_drift=max_drift,
            drift_per_symbol=drift_per_symbol,
            composition_changed=composition_changed,
            in_cooldown=in_cooldown,
            cooldown_remaining_days=cooldown_remaining,
            outside_model=tuple(outside_model),
        )

    # ── Trigger evaluation ──
    if force:
        return _decision(True, "force flag set")
    if last_basket is None:
        return _decision(True, "no prior basket recorded — cold start")

    if outside_model:
        if in_cooldown:
            return _decision(
                False,
                f"{outside_model_reason(outside_model)}, but in cooldown "
                f"({cooldown_remaining}d remaining)",
            )
        return _decision(True, outside_model_reason(outside_model))

    if composition_changed:
        if in_cooldown:
            return _decision(
                False,
                f"composition changed but in cooldown "
                f"({cooldown_remaining}d remaining)",
            )
        return _decision(
            True,
            "composition of materially-weighted basket changed since last submission",
        )

    if max_drift > drift_threshold:
        if in_cooldown:
            return _decision(
                False,
                f"max drift {max_drift:.4f} > {drift_threshold:.4f} "
                f"but in cooldown ({cooldown_remaining}d remaining)",
            )
        return _decision(
            True,
            f"max drift {max_drift:.4f} exceeds band {drift_threshold:.4f}",
        )

    return _decision(
        False,
        f"max drift {max_drift:.4f} within band {drift_threshold:.4f} — skip",
    )


__all__ = [
    "DriftDecision",
    "RebalanceDecision",
    "decide_rebalance",
    "decide_rebalance_with_drift",
    "is_month_end_window",
    "outside_model_reason",
]
