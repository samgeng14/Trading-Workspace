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
    report = {
        "ran_at": started.isoformat(timespec="seconds"),
        "profile": profile,
        "endpoint": f"{endpoint.host}:{endpoint.port}",
        "results": [{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix or ""} for r in results],
    }
    _doctor_path(profile).write_text(json.dumps(report, indent=2))
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
