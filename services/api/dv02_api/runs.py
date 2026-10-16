"""Daily runs (artifacts/runs/daily_run_*.json), today's schedule, and starting a run.

The LaunchAgent fires the daily runner Mon–Fri at 15:35 ET, ten minutes before
the engine's last market-on-close send (15:45 ET), so a rebalance fills at that
day's close. launchd takes local wall time: bin/tradeit schedule install writes
runner_time(), 15:35 ET in the Mac's zone (14:35 in Central time), or
DV02_RUNNER_TIME (local HH:MM) if set. The schedule shows the installed plist's
time when there is one.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import plistlib
import subprocess
import sys
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from . import engine, nyse

log = logging.getLogger(__name__)

LAUNCH_AGENT = "com.capitalfund.daily-runner"
STEP_LABELS = {
    "reconcile": "Reconcile",
    "append_daily": "ETF bars",
    "append_indices_daily": "Index bars",
    "append_futures_daily": "Futures bars",
    "generate_targets": "Targets",
    "build_basket": "Basket",
    "submit_orders": "Submit",
}


def _runs_dir() -> Path:
    return engine.artifacts_dir() / "runs"


def _local(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def _run_start(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, "%Y%m%d_%H%M%S").astimezone()  # runner uses local time
    except ValueError:
        return None


def _money(x: Any) -> str:
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return "?"


def _step_detail(name: str, step: dict[str, Any]) -> tuple[bool, str]:
    if step.get("skipped"):
        return True, f"skipped: {step.get('reason', 'not requested')}"
    if step.get("error"):
        return False, str(step["error"])
    ok = bool(step.get("ok", True))
    if name.startswith("append"):
        errs = step.get("errors") or []
        detail = f"{step.get('new_rows_added_to_master', 0)} new rows, latest {step.get('latest_date', '?')}"
        return ok, detail + (f"; {len(errs)} symbol errors" if errs else "")
    if name == "generate_targets":
        return ok, f"{step.get('optimizer', '?')}, rebalance date {step.get('latest_rebalance_date', '?')}"
    if name == "build_basket":
        return ok, (
            f"{step.get('orders_rows', 0)} orders on NAV {_money(step.get('portfolio_value'))}: "
            f"buy {_money(step.get('buy_dollars'))}, sell {_money(step.get('sell_dollars'))}"
        )
    if name == "reconcile":
        breaks = len(step.get("diffs") or [])
        if step.get("material_mismatch"):
            return False, f"{breaks} breaks against IBKR; kill switch armed (see {step.get('report_path')})"
        return ok, f"{breaks} small breaks against IBKR, under the threshold" if breaks else "book matches IBKR"
    if name == "submit_orders":
        if step.get("blocked"):
            return False, "blocked by pre-trade risk: " + "; ".join(step.get("reasons") or [])
        if step.get("submitted"):
            res = step.get("result") or {}
            rows = res.get("rows", res.get("submitted"))  # the runtime submit counts them in "rows"
            how = (res.get("execution") or {}).get("summary")
            detail = f"submitted {rows if rows is not None else ''} orders".replace("  ", " ")
            return ok, detail + (f", {how}" if how else "")
        return ok, str(step.get("reason") or ("ok" if ok else "failed"))
    return ok, "ok" if ok else "failed"


def _summary(doc: dict[str, Any]) -> str:
    text = _outcome(doc)
    off = doc.get("submit_disabled")  # config/execution.yaml made the run a dry run
    return f"{text}; orders are switched off ({off})" if off else text


def _outcome(doc: dict[str, Any]) -> str:
    if doc.get("error"):
        return f"Stopped: {doc['error']}"
    if doc.get("skipped"):
        return f"Skipped: {doc.get('reason') or 'nothing to do'}"
    submit = (doc.get("steps") or {}).get("submit_orders") or {}
    decision = doc.get("rebalance_decision") or {}
    if submit.get("submitted"):
        return "Rebalanced: orders submitted"
    if submit.get("blocked"):
        return "Rebalance blocked by pre-trade risk"
    if doc.get("should_rebalance") is False:
        reason = decision.get("reason") or "no drift above threshold"
        return f"No rebalance: {reason}"
    if doc.get("dry_run"):
        return "Dry run: basket built, nothing sent"
    return "OK" if doc.get("ok") else "Failed"


def load_runs(limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for path in sorted(_runs_dir().glob("daily_run_*.json"), reverse=True)[:limit]:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        run_id = str(doc.get("run_id") or path.stem.removeprefix("daily_run_"))
        started = _run_start(run_id) or datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        steps = []
        for name, step in (doc.get("steps") or {}).items():
            if isinstance(step, dict):
                ok, detail = _step_detail(name, step)
                steps.append({"name": STEP_LABELS.get(name, name), "ok": ok, "detail": detail})
        decision = doc.get("rebalance_decision")
        if isinstance(decision, dict):
            gate = "rebalance" if doc.get("should_rebalance") else "hold"
            steps.append(
                {"name": "Rebalance gate", "ok": True, "detail": f"{gate}: {decision.get('reason', '')}".strip(": ")}
            )
        out.append(
            {
                "id": f"daily-{run_id}",
                "kind": "daily",
                "started_at": _local(started),
                "finished_at": _local(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)),
                "ok": bool(doc.get("ok")),
                "dry_run": bool(doc.get("dry_run")),
                "summary": _summary(doc),
                "steps": steps,
                "_date": doc.get("date"),
                "_submitted": bool(((doc.get("steps") or {}).get("submit_orders") or {}).get("submitted")),
                "_orders_off": bool(doc.get("submit_disabled")),
            }
        )
    return out


def submitted_today(today: date) -> dict[str, Any] | None:
    """The run that already sent orders today, if any (the duplicate guard)."""
    for r in load_runs(10):
        if r["_date"] == today.isoformat() and r["_submitted"] and not r["dry_run"]:
            return r
    return None


# ── schedule ───────────────────────────────────────────────────────────


RUN_AT_ET = time(15, 35)


def runner_time(on: date | None = None) -> time:
    """When to schedule the daily run, in this machine's local wall time:
    DV02_RUNNER_TIME (HH:MM, local) if set, else RUN_AT_ET on ``on`` (default
    today) in the local zone. Zones that change clocks on the US dates keep the
    same ET time all year."""
    raw = os.environ.get("DV02_RUNNER_TIME")
    if raw:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    return nyse.et_time(on or datetime.now(nyse.ET).date(), RUN_AT_ET).astimezone().time()


def installed_time(weekday: int) -> time | None:
    """The installed LaunchAgent's local fire time on ``weekday`` (0 is Monday), if it has one."""
    times = (launch_agent() or {}).get("StartCalendarInterval") or []
    for t in [times] if isinstance(times, dict) else times:
        # launchd's Weekday: 0 and 7 are Sunday, 1 is Monday; no Weekday is every day
        if t.get("Weekday") is None or (t["Weekday"] - 1) % 7 == weekday:
            return time(t.get("Hour", 0), t.get("Minute", 0))
    return None


def orders_at(fire: datetime) -> Any:
    """How a run starting at ``fire`` sends its orders (the engine's choice: MOC
    until 15:45 ET, else market-on-open); on a day without a session, as the next
    trading day's run at the same time would."""
    from src.broker.ibkr.auction import execution_for

    day = nyse.et_date(fire)
    if not nyse.is_trading_day(day):
        fire += timedelta(days=(nyse.next_trading_day(day) - day).days)
    return execution_for(at=fire)


def schedule(now: datetime) -> list[dict[str, Any]]:
    local_now = now.astimezone()
    at = installed_time(local_now.weekday()) or runner_time(nyse.et_date(now))
    fire = datetime.combine(local_now.date(), at, tzinfo=local_now.tzinfo)
    orders = orders_at(fire)
    # a run execution.yaml turned into a dry run is still the day's scheduled run
    today_runs = [
        r
        for r in load_runs(10)
        if r["_date"] == local_now.date().isoformat() and (not r["dry_run"] or r["_orders_off"])
    ]
    job: dict[str, Any] = {
        "id": "daily_run",
        "label": f"Daily run: data, targets, rebalance if drift > 5%; orders go {orders.name}",
        "at": fire.isoformat(timespec="seconds"),
    }
    if RUN_STATE["running"]:
        job.update(status="running", detail=f"started from the dashboard at {RUN_STATE['started_at']}")
    elif today_runs:
        r = today_runs[0]
        job.update(status="done" if r["ok"] else "failed", detail=r["summary"])
    elif local_now.weekday() >= 5:
        job.update(status="skipped", detail="weekend")
    elif not nyse.is_trading_day(nyse.et_date(now)):
        job.update(status="skipped", detail="market holiday")
    elif local_now < fire:
        job.update(status="scheduled", detail=f"{fire.astimezone(nyse.ET):%H:%M} ET; {orders.summary()}")
    else:
        job.update(status="failed", detail="no run recorded yet today; is the Mac awake and the LaunchAgent installed?")
    return [job]


def launch_agent_installed() -> bool | None:
    """True/False on macOS; None where launchd doesn't exist."""
    if platform.system() != "Darwin":
        return None
    try:
        res = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCH_AGENT}"], capture_output=True, timeout=3
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.returncode == 0


# ── starting a run from the dashboard ──────────────────────────────────

RUN_STATE: dict[str, Any] = {"running": False, "started_at": None, "result": None, "log": None}
_run_lock = threading.Lock()


def launch_agent() -> dict[str, Any] | None:
    """The installed LaunchAgent's plist (python, args, environment), if any."""
    path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"
    try:
        return plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def runner_command(profile: str) -> tuple[list[str], dict[str, str]]:
    """The command and environment for a dashboard-started daily run.

    Uses the LaunchAgent's Python and environment (so CAPITALFUND_OPTIMIZER and
    the ports match the scheduled run), else DV02_ENGINE_PYTHON, else this
    interpreter.
    """
    from src.broker.ibkr import settings

    env = dict(os.environ)
    agent = launch_agent() or {}
    env.update({k: str(v) for k, v in (agent.get("EnvironmentVariables") or {}).items()})
    args = agent.get("ProgramArguments") or []
    python = os.environ.get("DV02_ENGINE_PYTHON") or (args[0] if args else None) or sys.executable
    endpoint = settings.resolve_endpoint(profile)
    cmd = [
        python, "-m", "src.production.daily_runner",
        "--profile", profile, "--host", endpoint.host, "--port", str(endpoint.port),
        # base 310 (+2, +4, +10 for its sub-steps) stays clear of the scheduled run's 101
        "--client-id", str(endpoint.client_id("backfill")),
        "--force-rebalance",
    ]  # fmt: skip
    return cmd, env


def start_daily_run(profile: str) -> bool:
    """Run the engine's daily runner now, forcing a rebalance; False if one is already running.

    The engine does the rest: fresh bars and targets, a basket from live
    positions, its pre-trade risk gate (kill switch included), then orders on
    the engine's own client ID. A successful run also makes the scheduled
    LaunchAgent run skip today, so nothing trades twice. It runs as a separate
    process, so a crash there can't take the API down.
    """
    if not _run_lock.acquire(blocking=False):
        return False
    cmd, env = runner_command(profile)
    log_path = engine.api_state_dir() / f"dashboard_run_{datetime.now():%Y%m%d_%H%M%S}.log"
    try:
        out = log_path.open("w")
        proc = subprocess.Popen(cmd, cwd=engine.ENGINE_ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
    except OSError as exc:
        _run_lock.release()
        raise RuntimeError(f"could not start the daily runner ({cmd[0]}): {exc}") from exc
    RUN_STATE.update(
        running=True, started_at=datetime.now().astimezone().strftime("%H:%M"), result=None, log=str(log_path)
    )

    def wait() -> None:
        try:
            code = proc.wait()
            RUN_STATE["result"] = {"ok": code == 0, "exit_code": code, "log": str(log_path)}
            if code != 0:
                log.warning("dashboard-started daily run exited %s; see %s", code, log_path)
        finally:
            out.close()
            RUN_STATE["running"] = False
            _run_lock.release()

    threading.Thread(target=wait, name="daily-run", daemon=True).start()
    return True
