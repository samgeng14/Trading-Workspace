"""
kill_switch.py — Persistent emergency-stop flag.

A simple, file-backed boolean. When ARMED, every code path that submits
orders MUST call ``require_kill_switch_clear()`` first and abort if the
switch is on.

The state lives at ``artifacts/runs/kill_switch.json`` so it survives
process restarts. Arming it here blocks new orders only: a basket already at
IBKR still fills. To stop the fund, use ``bin/tradeit kill "why"``
(src/execution/kill.py): it arms the switch, then cancels every working
order; ``bin/tradeit resume`` disarms it after the doctor and a reconcile
pass. The plain file operations stay here:

    python -m src.execution.kill_switch arm "manual halt for review"
    python -m src.execution.kill_switch disarm
    python -m src.execution.kill_switch status

The dashboard's kill switch toggle arms and disarms this same file.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KILL_SWITCH_PATH = PROJECT_ROOT / "artifacts" / "runs" / "kill_switch.json"

log = logging.getLogger(__name__)


class KillSwitchTripped(RuntimeError):
    """Raised when an order-submitting code path runs while the switch is armed."""


class KillSwitchCorrupted(RuntimeError):
    """Raised when the kill-switch state file exists but is unreadable.

    We FAIL CLOSED on corruption — assume the switch is armed rather
    than risk treating a half-written file as 'disarmed' and trading
    when an operator thought we wouldn't."""


@dataclass
class KillSwitchState:
    armed: bool
    reason: str
    armed_at: str | None
    last_change_by: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _default_state() -> KillSwitchState:
    return KillSwitchState(armed=False, reason="", armed_at=None, last_change_by=None)


def read_kill_switch_state() -> KillSwitchState:
    """Read current state from disk.

    Missing file -> disarmed (cold start).
    Corrupted file -> raises ``KillSwitchCorrupted``. Fail closed: we
    refuse to treat an unreadable file as 'disarmed' since that is the
    one failure mode an operator never wants.
    """
    if not KILL_SWITCH_PATH.exists():
        return _default_state()
    try:
        text = KILL_SWITCH_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        # Read failure is treated as corruption — better to fail loud
        # than to silently disarm.
        raise KillSwitchCorrupted(
            f"Could not read kill-switch state at {KILL_SWITCH_PATH}: {exc}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KillSwitchCorrupted(
            f"Kill-switch state file is not valid JSON ({KILL_SWITCH_PATH}): {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise KillSwitchCorrupted(
            f"Kill-switch state file root must be an object, got {type(data).__name__}"
        )
    return KillSwitchState(
        armed=bool(data.get("armed", False)),
        reason=str(data.get("reason", "") or ""),
        armed_at=data.get("armed_at"),
        last_change_by=data.get("last_change_by"),
    )


def _atomic_write(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` atomically.

    The write goes to a sibling tempfile + os.replace, so a crash mid-
    write leaves either the prior version or the new version on disk —
    never a half-written one. fsync ensures the bytes actually hit
    durable storage before the rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # Some filesystems (test tmpfs) don't support fsync.
                # Better to write than to fail.
                pass
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup if rename never happened.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_state(state: KillSwitchState) -> None:
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
    _atomic_write(KILL_SWITCH_PATH, payload)


def is_kill_switch_armed() -> bool:
    """Return True if armed. On corruption we conservatively return True
    (fail closed)."""
    try:
        return read_kill_switch_state().armed
    except KillSwitchCorrupted:
        log.error("Kill-switch state corrupted — treating as ARMED.")
        return True


def arm_kill_switch(reason: str, *, by: str | None = None) -> KillSwitchState:
    """Arm the kill switch. ``reason`` is required so operators can tell
    why it was tripped after the fact."""
    if not reason or not reason.strip():
        raise ValueError("Kill-switch reason cannot be empty")
    state = KillSwitchState(
        armed=True,
        reason=reason.strip(),
        armed_at=datetime.now().isoformat(timespec="seconds"),
        last_change_by=by or _whoami(),
    )
    _write_state(state)
    return state


def disarm_kill_switch(*, by: str | None = None) -> KillSwitchState:
    state = KillSwitchState(
        armed=False,
        reason="",
        armed_at=None,
        last_change_by=by or _whoami(),
    )
    _write_state(state)
    return state


def require_kill_switch_clear() -> None:
    """Call this at the top of every order-submitting function.

    Raises ``KillSwitchTripped`` if the switch is armed. The exception
    message includes the configured reason so the operator who looks at
    a failed run log can immediately see why submission was blocked.
    """
    try:
        state = read_kill_switch_state()
    except KillSwitchCorrupted as exc:
        # Fail CLOSED. An unreadable state file is treated as a tripped
        # switch, not as 'no state therefore disarmed'.
        raise KillSwitchTripped(
            f"Kill-switch state file unreadable; refusing to trade. ({exc})"
        ) from exc
    if state.armed:
        msg = f"Kill switch is ARMED: {state.reason or '(no reason set)'}"
        if state.armed_at:
            msg += f" (since {state.armed_at})"
        raise KillSwitchTripped(msg)


def _whoami() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return "unknown"


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.execution.kill_switch",
        description="Manage the trading-engine kill switch.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    arm = sub.add_parser("arm", help="Arm the kill switch (block trading).")
    arm.add_argument("reason", help="Required: why are you halting trading?")
    arm.add_argument("--by", help="Operator name. Defaults to current user.")

    disarm = sub.add_parser("disarm", help="Disarm the kill switch.")
    disarm.add_argument("--by", help="Operator name.")

    sub.add_parser("status", help="Show current state.")

    args = parser.parse_args()
    if args.cmd == "arm":
        s = arm_kill_switch(args.reason, by=args.by)
        print(f"ARMED at {s.armed_at} by {s.last_change_by}: {s.reason}")
        return 0
    if args.cmd == "disarm":
        s = disarm_kill_switch(by=args.by)
        print(f"DISARMED by {s.last_change_by}")
        return 0
    if args.cmd == "status":
        s = read_kill_switch_state()
        if s.armed:
            print(f"ARMED · {s.reason} · since {s.armed_at} · by {s.last_change_by}")
            return 1
        print("disarmed")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
