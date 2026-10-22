"""
risk_service.py — Pre-submission risk checks for the dashboard.

Runs a set of automated checks that the Risk Monitor screen and the Submit
Orders screen use to decide whether trading can proceed:

- Paper mode active
- IBKR connection reachable
- Required data files are present and fresh
- Basket file exists and has rows
- Duplicate-submission guard is clear
- Latest positions snapshot is available

Each check returns a consistent structured dict so the UI can group results
and compute pass/warn/fail counts without duplicating logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

_STATUS_ICON = {
    STATUS_PASS: "✅",
    STATUS_WARN: "⚠️",
    STATUS_FAIL: "❌",
}


def _make_check(
    name: str,
    status: str,
    detail: str,
    *,
    severity: str = "info",
) -> dict[str, Any]:
    return {
        "check": name,
        "status": status,
        "icon": _STATUS_ICON.get(status, ""),
        "detail": detail,
        "severity": severity,
    }


def _age_hours(ts_iso: str | None) -> float | None:
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(ts_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def _freshness_check(name: str, ts_iso: str | None, max_hours: float) -> dict[str, Any]:
    age = _age_hours(ts_iso)
    if age is None:
        return _make_check(name, STATUS_FAIL, "No timestamp", severity="critical")
    if age <= max_hours:
        return _make_check(name, STATUS_PASS, f"{age:.1f}h old")
    return _make_check(
        name,
        STATUS_WARN,
        f"{age:.1f}h old (max {max_hours:.0f}h)",
        severity="minor",
    )


def check_paper_mode(profile: str) -> dict[str, Any]:
    mode = (profile or "").lower()
    if mode == "paper":
        return _make_check("Paper mode", STATUS_PASS, "Paper trading is active")
    return _make_check(
        "Paper mode",
        STATUS_WARN,
        f"Current mode: {mode or 'unknown'}",
        severity="minor",
    )


def check_broker_connection(profile: str = "paper") -> dict[str, Any]:
    try:
        from src.dashboard.services.broker_service import get_broker_status

        status = get_broker_status(profile=profile)
    except Exception as exc:
        return _make_check(
            "IBKR connection",
            STATUS_FAIL,
            f"Broker status probe failed: {exc}",
            severity="critical",
        )

    if status.get("connected"):
        host = status.get("host")
        port = status.get("port")
        return _make_check(
            "IBKR connection",
            STATUS_PASS,
            f"Connected to {host}:{port}",
        )
    return _make_check(
        "IBKR connection",
        STATUS_FAIL,
        status.get("error") or "Gateway not reachable",
        severity="critical",
    )


def check_data_files(profile: str = "paper") -> list[dict[str, Any]]:
    from src.dashboard.services.pipeline_service import get_pipeline_status

    pipeline = {}
    try:
        pipeline = get_pipeline_status(profile=profile)
    except Exception as exc:
        return [
            _make_check(
                "Pipeline status",
                STATUS_FAIL,
                f"Could not read pipeline status: {exc}",
                severity="critical",
            )
        ]

    checks = [
        _make_check(
            "Prices file",
            STATUS_PASS if pipeline.get("prices_path") else STATUS_FAIL,
            pipeline.get("prices_path") or "Missing",
            severity="info" if pipeline.get("prices_path") else "critical",
        ),
        _make_check(
            "Targets file",
            STATUS_PASS if pipeline.get("targets_path") else STATUS_FAIL,
            pipeline.get("targets_path") or "Missing",
            severity="info" if pipeline.get("targets_path") else "critical",
        ),
        _make_check(
            "Basket file",
            STATUS_PASS if pipeline.get("basket_path") else STATUS_WARN,
            pipeline.get("basket_path") or "Not built yet",
            severity="info" if pipeline.get("basket_path") else "minor",
        ),
        _freshness_check("Prices freshness", pipeline.get("prices_timestamp"), 72),
        _freshness_check("Targets freshness", pipeline.get("targets_timestamp"), 72),
        _freshness_check("Basket freshness", pipeline.get("basket_timestamp"), 24),
    ]
    return checks


def check_basket_rows() -> dict[str, Any]:
    try:
        from src.production.runtime.order_service import load_latest_basket

        basket = load_latest_basket()
    except Exception as exc:
        return _make_check(
            "Basket has rows",
            STATUS_WARN,
            f"Could not load basket: {exc}",
            severity="minor",
        )
    if basket is None:
        return _make_check(
            "Basket has rows",
            STATUS_WARN,
            "No basket loaded",
            severity="minor",
        )
    try:
        rows = basket.height  # polars
    except AttributeError:
        rows = len(basket)
    if rows > 0:
        return _make_check("Basket has rows", STATUS_PASS, f"{rows} rows")
    return _make_check(
        "Basket has rows",
        STATUS_WARN,
        "Basket file is empty",
        severity="minor",
    )


def check_positions(profile: str = "paper") -> dict[str, Any]:
    try:
        from src.dashboard.services.broker_service import load_latest_positions

        df = load_latest_positions(profile=profile)
    except Exception as exc:
        return _make_check(
            "Position snapshot",
            STATUS_WARN,
            f"Could not load positions: {exc}",
            severity="minor",
        )
    if df is None:
        return _make_check(
            "Position snapshot",
            STATUS_WARN,
            "No saved snapshot",
            severity="minor",
        )
    rows = len(df)
    if rows > 0:
        return _make_check(
            "Position snapshot",
            STATUS_PASS,
            f"{rows} rows in snapshot",
        )
    return _make_check(
        "Position snapshot",
        STATUS_WARN,
        "Snapshot is empty",
        severity="minor",
    )


def check_duplicate_guard(profile: str = "paper") -> dict[str, Any]:
    try:
        from src.production.runtime.order_service import duplicate_submission_guard

        guard = duplicate_submission_guard(profile=profile)
    except Exception as exc:
        return _make_check(
            "Duplicate submit guard",
            STATUS_FAIL,
            f"Guard probe failed: {exc}",
            severity="critical",
        )

    allowed = guard.get("allowed")
    reason = guard.get("reason", "unknown")
    if allowed is True:
        return _make_check("Duplicate submit guard", STATUS_PASS, reason)
    if allowed is False:
        return _make_check(
            "Duplicate submit guard",
            STATUS_WARN,
            reason,
            severity="minor",
        )
    return _make_check(
        "Duplicate submit guard",
        STATUS_FAIL,
        reason,
        severity="critical",
    )


def run_all_checks(profile: str = "paper") -> list[dict[str, Any]]:
    """Run every pre-submission check and return results in a stable order."""
    checks: list[dict[str, Any]] = [check_paper_mode(profile)]
    checks.append(check_broker_connection(profile))
    checks.extend(check_data_files(profile))
    checks.append(check_duplicate_guard(profile))
    checks.append(check_basket_rows())
    checks.append(check_positions(profile))
    return checks


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {STATUS_PASS: 0, STATUS_WARN: 0, STATUS_FAIL: 0}
    for check in checks:
        status = check.get("status")
        if status in summary:
            summary[status] += 1
    return summary


def is_submission_safe(checks: list[dict[str, Any]]) -> bool:
    """Submission is considered safe when no ``fail`` checks remain."""
    return not any(c.get("status") == STATUS_FAIL for c in checks)
