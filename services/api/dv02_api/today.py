"""Did the fund trade today? One plain answer for the Trading page and the Command Center.

A session counts as traded when a real run (not a dry run) sent orders for it. A run belongs
to the session its orders execute in: one sent after hours, market-on-open, belongs to the
next session (the 00:35 Central run on Oct 16 traded at the Oct 16 open), and the 15:35 ET
run that day then skips. The answer says when the orders went, which auction fills them, and
how many filled: from IBKR when this machine is connected, else from the end-of-day
reconcile (which counts the fills IBKR reported), else only that they were sent.

A rebalance started from the dashboard is the daily runner itself; its steps come from the
runner's progress file while it runs.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import engine, nyse, orders, runs

# How long after the scheduled run's start the API waits for its log before calling it missed.
MISSED_AFTER = timedelta(minutes=15)

STEPS = (
    ("reconcile", "Reconcile with IBKR"),
    ("append_daily", "Fresh prices"),
    ("generate_targets", "Targets"),
    ("rebalance_decision", "Rebalance decision"),
    ("build_basket", "Basket"),
    ("submit_orders", "Checks and orders"),
)
DOING = {
    "reconcile": "reconciling with IBKR",
    "append_daily": "fetching fresh prices",
    "generate_targets": "computing targets",
    "rebalance_decision": "deciding what to trade",
    "build_basket": "building the basket",
    "submit_orders": "running the checks and sending orders",
}


def _hm(at: datetime, day: date) -> str:
    """ "01:35 ET" on ``day`` itself, "Thu 20:05 ET" on another day."""
    et = at.astimezone(nyse.ET)
    return f"{et:%H:%M} ET" if et.date() == day else f"{et:%a %H:%M} ET"


def _started(doc: dict[str, Any]) -> datetime | None:
    return runs._run_start(str(doc.get("run_id") or ""))


def _submit(doc: dict[str, Any]) -> dict[str, Any]:
    return (doc.get("steps") or {}).get("submit_orders") or {}


def _execution(doc: dict[str, Any]) -> dict[str, Any]:
    return (_submit(doc).get("result") or {}).get("execution") or {}


def session_of(doc: dict[str, Any]) -> date | None:
    """The session a run's orders execute in; for a run that sent none, the day it ran."""
    for raw in (_execution(doc).get("session"), doc.get("date")):
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            continue
    return None


def run_docs(limit: int = 20) -> list[dict[str, Any]]:
    """The newest run logs, raw."""
    out = []
    for path in sorted((engine.artifacts_dir() / "runs").glob("daily_run_*.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return out


def _kind(doc: dict[str, Any]) -> str | None:
    """submitted, held, off (orders switched off), failed; None for runs that say nothing
    about trading (a closed-day skip, a dry run someone asked for)."""
    if doc.get("skipped"):
        return None
    sub = _submit(doc)
    if sub.get("submitted") and not doc.get("dry_run"):
        return "submitted"
    if doc.get("ok") and doc.get("should_rebalance") is False:
        return "held"
    if doc.get("submit_disabled"):
        return "off"
    if doc.get("dry_run"):
        return None
    return "failed" if not doc.get("ok") or sub.get("blocked") else None


def _why_stopped(doc: dict[str, Any]) -> str:
    sub = _submit(doc)
    if sub.get("blocked"):
        return "a pre-trade check blocked the orders (" + "; ".join(sub.get("reasons") or []) + ")"
    for name, step in (doc.get("steps") or {}).items():
        if isinstance(step, dict) and (step.get("error") or step.get("ok") is False):
            return f"{runs.STEP_LABELS.get(name, name).lower()} failed: {step.get('error') or 'see the run log'}"
    return str(doc.get("error") or "see the run log")


# ── orders and fills ───────────────────────────────────────────────────


def _submission(doc: dict[str, Any]) -> list[dict[str, str]]:
    """The rows of the run's submission file, found by name in this machine's data folder
    (the run log holds the path on the machine that ran it)."""
    sub = _submit(doc)
    raw = sub.get("submission_path") or (sub.get("result") or {}).get("submission_path")
    if not raw:
        return []
    path = engine.data_dir() / "broker" / "orders" / Path(str(raw)).name
    try:
        with path.open() as f:
            return [r for r in csv.DictReader(f) if r.get("status") not in ("DRY_RUN", "ERROR")]
    except OSError:
        return []


def order_counts(doc: dict[str, Any], profile: str, day: date, live: list[dict[str, Any]] | None) -> dict[str, Any]:
    """How far the run's orders got. ``live`` is IBKR's order list when connected."""
    rows = _submission(doc)
    perm = {str(r.get("perm_id") or r.get("order_id") or "") for r in rows} - {""}
    sent = len(rows) or int((_submit(doc).get("result") or {}).get("rows") or 0)
    ex = _execution(doc)
    out: dict[str, Any] = {"sent": sent, "filled": 0, "working": sent, "cancelled": 0, "source": "submission"}
    if ex.get("auction") in ("open", "close"):
        out["auction"] = ex["auction"]
    if ex.get("fills_at"):
        out["fills_at"] = ex["fills_at"]

    mine = [o for o in live or [] if o["id"] in perm]
    if mine:
        out.update(
            source="ibkr",
            filled=sum(o["status"] == "filled" for o in mine),
            working=sum(o["status"] in ("pending", "submitted", "partial") for o in mine),
            cancelled=sum(o["status"] in ("cancelled", "rejected") for o in mine),
        )
        out["working"] += sent - len(mine)  # sent, and not (yet) in IBKR's list
        return out

    reconciled = {k: v for k, v in orders.reconcile_fills(profile, day.isoformat()).items() if k in perm}
    if reconciled:
        filled = sum(v["filled"] >= v["quantity"] > 0 for v in reconciled.values())
        out.update(source="reconcile", filled=filled, working=max(0, sent - filled))
    return out


# ── the answer ─────────────────────────────────────────────────────────


def _steps(progress: dict[str, Any] | None, counts: dict[str, Any] | None) -> list[dict[str, str]]:
    if not progress:
        return []
    done_steps = progress.get("steps") or {}
    current = progress.get("current")
    finished = bool(progress.get("finished"))
    out = []
    for key, label in STEPS:
        if key == "rebalance_decision":
            decided = "should_rebalance" in progress
            reason = (progress.get("rebalance_decision") or {}).get("reason") or ""
            if decided:
                go = progress.get("should_rebalance")
                out.append(
                    {
                        "id": key,
                        "label": label,
                        "state": "done",
                        "detail": ("rebalance" if go else "nothing to trade") + (f": {reason}" if reason else ""),
                    }
                )
                continue
            step = None
        else:
            step = done_steps.get(key)
        if isinstance(step, dict):
            ok, detail = runs._step_detail(key, step)
            state = "skipped" if step.get("skipped") else "done" if ok else "failed"
            if key == "submit_orders" and ok and step.get("submitted"):
                detail = "checks passed; " + detail
            out.append({"id": key, "label": label, "state": state, "detail": detail})
        elif current == key and not finished:
            out.append({"id": key, "label": label, "state": "running", "detail": DOING[key]})
        else:
            out.append({"id": key, "label": label, "state": "skipped" if finished else "pending", "detail": ""})
    if counts and counts["sent"]:
        all_in = counts["filled"] >= counts["sent"]
        out.append(
            {
                "id": "fills",
                "label": "Fills",
                "state": "done" if all_in else "running",
                "detail": f"filled {counts['filled']}/{counts['sent']}",
            }
        )
    return out


def trading_day(
    *,
    profile: str,
    now: datetime,
    connected: bool,
    live_orders: list[dict[str, Any]] | None,
    snapshot_at: datetime | None,
    kill_armed: bool,
) -> dict[str, Any]:
    today = nyse.et_date(now)
    open_today = nyse.is_trading_day(today)
    day = today if open_today else nyse.next_trading_day(today)
    fire = nyse.et_time(today, runs.RUN_AT_ET)
    next_run = fire if open_today and now < fire else nyse.et_time(nyse.next_trading_day(today), runs.RUN_AT_ET)

    doc: dict[str, Any] = {
        "as_of": now.isoformat(timespec="seconds"),
        "trading_date": day.isoformat(),
        "next_run_at": next_run.isoformat(timespec="seconds"),
        "connected": connected,
        "steps": [],
    }
    if not connected:
        host = os.environ.get("DV02_TRADING_HOST", "").strip()
        where = f"Trading runs on {host}." if host else "Trading runs on the machine that holds the IBKR gateway."
        if snapshot_at is not None:
            doc["snapshot_at"] = snapshot_at.isoformat(timespec="seconds")
            doc["note"] = (
                f"IBKR isn't connected on this machine, so the pages show the snapshot from "
                f"{_hm(snapshot_at, today)}. {where}"
            )
        else:
            doc["note"] = f"IBKR isn't connected on this machine. {where}"

    progress = runs.dashboard_progress()
    docs = [d for d in run_docs() if session_of(d) == day and _kind(d)]
    sent = next((d for d in docs if _kind(d) == "submitted"), None)
    counts = order_counts(sent, profile, day, live_orders if connected else None) if sent else None
    if progress and session_of(progress) not in (day, None):
        progress = None  # a dashboard run for another session
    same_run = bool(progress and sent and sent.get("run_id") == progress.get("run_id"))
    doc["steps"] = _steps(progress, counts if same_run else None)

    running = runs.RUN_STATE["running"] and not (progress or {}).get("finished")
    if not open_today:
        state = "closed"
        doc["headline"] = "No trading today: the market is closed"
        doc["detail"] = f"The next automatic run is {_hm(next_run, today)}."
    elif running:
        state = "running"
        current = (progress or {}).get("current")
        doc["headline"] = f"Rebalancing now: {DOING.get(current, 'starting the run')}"
        started = runs.RUN_STATE.get("started_iso")
        when = _hm(datetime.fromisoformat(started), today) if started else "just now"
        doc["detail"] = f"Started {when} from the dashboard: the daily run's own steps and checks."
    elif sent is not None:
        state = "done"
        at = _started(sent)
        when = _hm(at, today) if at else "earlier"
        auction = counts.get("auction") if counts else None
        fills_at = datetime.fromisoformat(counts["fills_at"]) if counts and counts.get("fills_at") else None
        filled_all = counts is not None and counts["sent"] > 0 and counts["filled"] >= counts["sent"]
        if auction == "open":
            doc["headline"] = f"Traded at today's open (orders sent {when})"
        elif auction == "close" and (filled_all or (fills_at and now >= fills_at)):
            doc["headline"] = f"Traded at today's close (orders sent {when})"
        elif auction == "close":
            doc["headline"] = f"Orders in for today's close (sent {when})"
        else:
            doc["headline"] = f"Traded today (orders sent {when})"
        doc["detail"] = _fills_line(counts, fills_at, now)
        doc["orders"] = counts
        doc["run_started_at"] = at.isoformat(timespec="seconds") if at else None
    elif docs:
        last = docs[0]
        at = _started(last)
        when = _hm(at, today) if at else "today's"
        doc["run_started_at"] = at.isoformat(timespec="seconds") if at else None
        kind = _kind(last)
        if kind == "held":
            state = "held"
            reason = (last.get("rebalance_decision") or {}).get("reason") or "the book is within its drift bands"
            doc["headline"] = "No trade needed today"
            doc["detail"] = f"The {when} run checked the book: {reason}."
        elif kind == "off":
            state = "failed"
            doc["headline"] = "Not traded today: orders are switched off"
            doc["detail"] = f"The {when} run was a dry run ({last.get('submit_disabled')})."
        else:
            state = "failed"
            doc["headline"] = "Not traded today: the run stopped"
            doc["detail"] = f"The {when} run stopped before sending orders: {_why_stopped(last)}."
    elif runs.RUN_STATE.get("result") and not runs.RUN_STATE["result"].get("ok"):
        state = "failed"
        doc["headline"] = "Not traded today: the rebalance stopped"
        code = runs.RUN_STATE["result"].get("exit_code")
        doc["detail"] = f"The run started from the dashboard exited with code {code}; its log is on this machine."
    elif now >= fire + MISSED_AFTER and connected:
        state = "failed"
        doc["headline"] = "Not traded today: no run since 15:35 ET"
        doc["detail"] = f"No run recorded for today; {runs.MISSED_HINT[runs.scheduler()]}"
    else:
        state = "not_yet"
        doc["headline"] = "Not traded yet today"
        how = runs.orders_at(next_run).summary() if now < fire else "it trades at the next session"
        doc["detail"] = f"The automatic run is at {_hm(next_run, today)}: {how}."
    doc["state"] = state

    reason = None
    if state in ("not_yet", "held", "failed"):
        if profile != "paper":
            reason = "Rebalancing from the dashboard is paper-only."
        elif not connected:
            reason = "IBKR isn't connected on this machine."
        elif kill_armed:
            reason = "The kill switch is armed: resume trading first."
        elif runs.RUN_STATE["running"]:
            reason = "A run is already working."
    elif state == "done":
        reason = "This session's orders are already in."
    elif state == "running":
        reason = "A run is already working."
    elif state == "closed":
        reason = "The market is closed today."
    doc["can_rebalance"] = reason is None
    if reason:
        doc["blocked_reason"] = reason
    return {k: v for k, v in doc.items() if v is not None}


def _fills_line(counts: dict[str, Any] | None, fills_at: datetime | None, now: datetime) -> str:
    if not counts or not counts["sent"]:
        return "The run sent its orders."
    n, filled = counts["sent"], counts["filled"]
    at = f" at the {fills_at.astimezone(nyse.ET):%H:%M} ET {counts.get('auction', 'auction')}" if fills_at else ""
    if counts["source"] == "submission":
        waiting = fills_at is not None and now < fills_at
        tail = "" if waiting else "; the fills show once IBKR is connected here or the end-of-day reconcile runs"
        return f"{n} orders sent{', filling' + at if waiting else ''}{tail}."
    if filled >= n:
        return f"Filled {filled}/{n}{at}."
    parts = [f"Filled {filled}/{n}"]
    if counts["working"]:
        parts.append(f"{counts['working']} working" + (f", they fill{at}" if fills_at and now < fills_at else ""))
    if counts["cancelled"]:
        parts.append(f"{counts['cancelled']} cancelled or rejected")
    return "; ".join(parts) + "."
