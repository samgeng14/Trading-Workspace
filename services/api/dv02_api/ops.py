"""Kill switch and IBKR doctor, through trading-engine's own modules.

The kill switch is the engine's file (artifacts/runs/kill_switch.json), so
arming it here stops the daily runner too, and arming it from the CLI shows
up here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from . import engine

log = logging.getLogger(__name__)


def _iso_local(value: str | None) -> str | None:
    """The engine stores naive local times; send them with an offset."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.astimezone()).isoformat(timespec="seconds")


def kill_switch() -> dict[str, Any]:
    from src.execution import kill_switch as ks  # trading-engine

    try:
        state = ks.read_kill_switch_state()
    except ks.KillSwitchCorrupted as exc:
        # The engine fails closed on a corrupt file; so does the dashboard.
        return {"armed": True, "reason": f"state file unreadable, treated as armed: {exc}"}
    out: dict[str, Any] = {"armed": state.armed}
    if state.reason:
        out["reason"] = state.reason
    changed = _iso_local(state.armed_at)
    if changed:
        out["changed_at"] = changed
    if state.last_change_by:
        out["changed_by"] = state.last_change_by
    return out


def set_kill_switch(armed: bool, reason: str | None, by: str) -> dict[str, Any]:
    from src.execution import kill_switch as ks

    if armed:
        ks.arm_kill_switch(reason or "", by=by)
    else:
        ks.disarm_kill_switch(by=by)
    return kill_switch()


def kill_switch_clear() -> bool:
    from src.execution import kill_switch as ks

    try:
        return not ks.read_kill_switch_state().armed
    except ks.KillSwitchCorrupted:
        return False


# ── the store ──────────────────────────────────────────────────────────

# What the health panel says of the store (trading-engine/src/storage). The upkeep
# loop refreshes it off the event loop: a store that is down takes seconds to say so.
_store_health: dict[str, str] = {"status": "unknown", "detail": "not checked yet"}


def check_store() -> dict[str, str]:
    """Is the store on here, and does it answer? Blocking: run it in a thread."""
    from src.storage.db import connect, load_config  # trading-engine

    cfg = load_config()
    if not cfg.enabled:
        why = f"the store is off here ({cfg.off_because}); the engine's files are the record"
        return {"status": "unknown", "detail": why}
    try:
        with connect(cfg) as conn:
            orders, fills, through = conn.execute(
                "SELECT (SELECT count(*) FROM orders), (SELECT count(*) FROM fills), (SELECT max(nav_date) FROM nav)"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — the panel says why; the files still keep the record
        why = f"Postgres at {cfg.where} doesn't answer ({type(exc).__name__}): the engine's files keep the record"
        return {"status": "warning", "detail": why}
    return {"status": "ok", "detail": f"Postgres at {cfg.where}: {orders} orders, {fills} fills, NAV through {through}"}


async def refresh_store_health() -> dict[str, str]:
    global _store_health
    _store_health = await asyncio.to_thread(check_store)
    return _store_health


def store_health() -> dict[str, str]:
    return dict(_store_health)


# ── doctor ─────────────────────────────────────────────────────────────


def _doctor_path(profile: str):
    return engine.api_state_dir() / f"doctor_{profile}.json"


def last_doctor(profile: str) -> dict[str, Any] | None:
    path = _doctor_path(profile)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _run_doctor_blocking(profile: str) -> dict[str, Any]:
    # ib_async's sync API needs an event loop in this worker thread.
    asyncio.set_event_loop(asyncio.new_event_loop())
    from src.broker.ibkr import doctor, settings  # trading-engine

    endpoint = settings.resolve_endpoint(profile)
    started = datetime.now(timezone.utc)
    results = doctor.run_doctor(profile)
    report = doctor.report(profile, results, ran_at=started, endpoint=f"{endpoint.host}:{endpoint.port}")
    doctor.save_report(report, engine.artifacts_dir())  # where _doctor_path reads it, and the day's copy
    return report


_doctor_lock = asyncio.Lock()


async def run_doctor(profile: str) -> dict[str, Any]:
    """Run the doctor in a worker thread (it uses its own client ID, 309 on paper)."""
    async with _doctor_lock:
        return await asyncio.to_thread(_run_doctor_blocking, profile)


def not_run_report(profile: str) -> dict[str, Any]:
    from src.broker.ibkr import settings

    endpoint = settings.resolve_endpoint(profile)
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile,
        "endpoint": f"{endpoint.host}:{endpoint.port}",
        "results": [
            {
                "name": "doctor",
                "status": "SKIP",
                "detail": "not run yet on this machine",
                "fix": "Press Run doctor, or run: python -m src.broker.ibkr.doctor",
            }
        ],
    }
