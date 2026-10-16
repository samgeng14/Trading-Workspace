"""
kill.py — Stop the fund now, and start it again only on a verified book.

    python -m src.execution.kill kill "why"    bin/tradeit kill "why"
    python -m src.execution.kill resume        bin/tradeit resume
    python -m src.execution.kill status        bin/tradeit status

Arming the kill switch (kill_switch.py) only blocks new orders: a
market-on-close basket already sent still fills at the close. ``kill`` arms
the switch first, so nothing new goes out whatever happens next, then
connects as the client that placed the orders (IBKR lets only that client
cancel them: the "orders" role, 300 on paper) and cancels every order IBKR
still holds for it. Orders placed by another client ID are reported, not
cancelled: only that client, or IB Gateway's own window, can.

``resume`` disarms the switch only when the IBKR doctor
(src/broker/ibkr/doctor.py) finds nothing failing and the end-of-day
reconcile (src/runtime/end_of_day_reconcile.py) finds no material break, so
trading restarts on a book that matches the broker. ``status`` shows the
switch, the orders IBKR holds now and the last reconcile.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.execution.kill_switch import (
    KillSwitchCorrupted,
    arm_kill_switch,
    disarm_kill_switch,
    read_kill_switch_state,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger(__name__)

# IBKR's order statuses once a cancel took, and once a fill beat it.
CANCELLED = {"Cancelled", "ApiCancelled", "Inactive"}
FILLED = {"Filled"}
CANCEL_WAIT = 10.0  # seconds to wait for IBKR to confirm the cancels


def _order_row(trade: Any) -> dict[str, Any]:
    order, contract, status = trade.order, trade.contract, trade.orderStatus
    return {
        "symbol": getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None),
        "action": getattr(order, "action", None),
        "quantity": getattr(order, "totalQuantity", None),
        "order_type": getattr(order, "orderType", None),
        "order_id": getattr(order, "orderId", None),
        "perm_id": getattr(order, "permId", None),
        "client_id": getattr(order, "clientId", None),
        "status": getattr(status, "status", None),
        "filled": getattr(status, "filled", None),
    }


def _describe(row: dict[str, Any]) -> str:
    return f"{row['action']} {row['quantity']:g} {row['symbol']} ({row['status']})"


# ── kill ──────────────────────────────────────────────────────────────────


@dataclass
class KillReport:
    switch: dict[str, Any]
    cancelled: list[dict[str, Any]] = field(default_factory=list)  # confirmed cancelled
    pending: list[dict[str, Any]] = field(default_factory=list)  # cancel sent, not confirmed in time
    filled: list[dict[str, Any]] = field(default_factory=list)  # filled before the cancel could take
    not_ours: list[dict[str, Any]] = field(default_factory=list)  # another client's: only it can cancel them
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Armed, and nothing IBKR holds is still working."""
        return self.error is None and not self.pending and not self.not_ours

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}

    def describe(self) -> str:
        out = [f"Kill switch ARMED: {self.switch.get('reason')} (by {self.switch.get('last_change_by')})"]
        if self.cancelled:
            out.append(f"Cancelled {len(self.cancelled)} working orders: " + ", ".join(map(_describe, self.cancelled)))
        if self.filled:
            out.append("Filled before the cancel could take: " + ", ".join(map(_describe, self.filled)))
        if self.pending:
            out.append(
                f"Cancel sent but not confirmed within {CANCEL_WAIT:.0f}s, check IB Gateway: "
                + ", ".join(map(_describe, self.pending))
            )
        if self.not_ours:
            out.append(
                "Placed by another client, so not cancelled from here (cancel them in IB Gateway or TWS): "
                + ", ".join(f"{_describe(r)} client {r['client_id']}" for r in self.not_ours)
            )
        if self.error:
            out.append(self.error)
        elif not (self.cancelled or self.filled or self.pending or self.not_ours):
            out.append("IBKR holds no working orders.")
        return "\n".join(out)


def kill(
    reason: str,
    *,
    profile: str = "paper",
    by: str | None = None,
    client: Any = None,
    host: str | None = None,
    port: int | None = None,
    wait: float = CANCEL_WAIT,
) -> KillReport:
    """Arm the kill switch, then cancel every order IBKR holds for the orders client.

    The switch is armed before anything connects, so the pre-trade gate blocks
    every new order whatever happens next. ``client`` is an unconnected IBKR
    client on the "orders" role (the default); this connects and disconnects it.
    """
    state = arm_kill_switch(reason, by=by)
    report = KillReport(switch=state.to_dict())
    if client is None:
        from src.broker.ibkr.settings import make_client

        client = make_client(profile, "orders", host=host, port=port)
    ours = client.config.client_id
    try:
        client.connect()
    except Exception as exc:  # noqa: BLE001
        report.error = (
            f"The switch is armed, but IBKR could not be reached as client {ours}, which placed the orders "
            f"({exc or type(exc).__name__}). If the daily run is in progress, wait for it and run kill "
            "again; otherwise cancel any working orders in IB Gateway or TWS."
        )
        return report
    try:
        ib = client.ib
        try:
            ib.reqAllOpenOrders()  # every client's open orders, so the others can be reported
        except Exception as exc:  # noqa: BLE001
            log.warning("kill: reqAllOpenOrders failed (%s); cancelling what this client sees", exc)
        mine = []
        for trade in client.open_trades():
            if getattr(trade.order, "clientId", ours) == ours:
                ib.cancelOrder(trade.order)
                mine.append(trade)
            else:
                report.not_ours.append(_order_row(trade))
        deadline = time.monotonic() + wait
        while mine and time.monotonic() < deadline:
            if all(trade.orderStatus.status in CANCELLED | FILLED for trade in mine):
                break
            client.sleep(0.25)
        for trade in mine:
            status = trade.orderStatus.status
            bucket = report.cancelled if status in CANCELLED else report.filled if status in FILLED else report.pending
            bucket.append(_order_row(trade))
    except Exception as exc:  # noqa: BLE001
        report.error = (
            f"The switch is armed, but cancelling failed ({type(exc).__name__}: {exc}). "
            "Check the working orders in IB Gateway or TWS."
        )
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return report


# ── resume ────────────────────────────────────────────────────────────────


@dataclass
class ResumeReport:
    disarmed: bool
    reasons: list[str] = field(default_factory=list)  # why the switch stays armed
    doctor: list[dict[str, Any]] = field(default_factory=list)  # the doctor's checks that didn't pass
    reconcile: dict[str, Any] | None = None
    switch: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        if self.disarmed:
            return "Resumed: the kill switch is disarmed. The doctor passed and the book matches IBKR."
        out = ["Not resumed; the kill switch stays armed:"] + [f"  - {r}" for r in self.reasons]
        for check in self.doctor:
            out.append(f"  doctor {check['status']} {check['name']}: {check['detail']}" + (f" ({check['fix']})" if check.get("fix") else ""))
        return "\n".join(out)


def resume(
    *,
    profile: str = "paper",
    by: str | None = None,
    doctor: Callable[[], list[Any]] | None = None,
    reconcile: Callable[[], Any] | None = None,
    host: str | None = None,
    port: int | None = None,
) -> ResumeReport:
    """Disarm the kill switch, but only after the doctor finds nothing failing and
    the reconcile finds no material break. ``doctor`` and ``reconcile`` default
    to the real ones on ``profile``; tests inject fakes."""
    from src.broker.ibkr.doctor import FAIL, PASS

    report = ResumeReport(disarmed=False)
    if doctor is None:
        from src.broker.ibkr.doctor import run_doctor

        def doctor() -> list[Any]:
            return run_doctor(profile, host=host, port=port)

    if reconcile is None:
        from src.broker.ibkr.settings import make_client
        from src.runtime.end_of_day_reconcile import reconcile_end_of_day

        def reconcile() -> Any:
            client = make_client(profile, "reconcile", host=host, port=port, readonly=True)
            return reconcile_end_of_day(profile=profile, client=client, root=PROJECT_ROOT)

    try:
        checks = doctor()
    except Exception as exc:  # noqa: BLE001
        report.reasons.append(f"the doctor could not run: {type(exc).__name__}: {exc}")
        checks = []
    report.doctor = [
        {"name": c.name, "status": c.status, "detail": c.detail, "fix": getattr(c, "fix", "")}
        for c in checks
        if c.status != PASS
    ]
    failing = [c for c in checks if c.status == FAIL]
    if failing:
        report.reasons.append("the doctor found " + "; ".join(f"{c.name}: {c.detail}" for c in failing))

    try:
        res = reconcile()
    except Exception as exc:  # noqa: BLE001
        report.reasons.append(f"the reconcile could not run: {type(exc).__name__}: {exc}")
        res = None
    if res is not None:
        report.reconcile = res.to_dict()
        if not res.ok:
            report.reasons.append(f"the reconcile could not run: {res.error}")
        elif res.skipped and not res.skipped.startswith("no expected book"):
            report.reasons.append(f"the reconcile is waiting: {res.skipped}")
        elif res.material_mismatch:
            report.reasons.append(
                f"the reconcile found {len(res.diffs)} break(s) against IBKR (see {res.report_path}); "
                "the book doesn't match the broker"
            )
    if report.reasons:
        report.switch = read_kill_switch_state().to_dict()
        return report
    report.switch = disarm_kill_switch(by=by).to_dict()
    report.disarmed = True
    return report


# ── status ────────────────────────────────────────────────────────────────


@dataclass
class StatusReport:
    switch: dict[str, Any]
    working: list[dict[str, Any]] | None = None  # the orders IBKR holds now; None when unreachable
    error: str | None = None
    last_reconcile: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        s = self.switch
        out = [
            f"Kill switch: ARMED since {s.get('armed_at')} by {s.get('last_change_by')}: {s.get('reason')}"
            if s.get("armed")
            else "Kill switch: disarmed"
        ]
        if self.working is None:
            out.append(f"IBKR: {self.error}")
        elif self.working:
            out.append(f"IBKR holds {len(self.working)} working orders: " + ", ".join(map(_describe, self.working)))
        else:
            out.append("IBKR holds no working orders.")
        r = self.last_reconcile
        if r is None:
            out.append("No reconcile report yet.")
        elif r.get("error"):
            out.append(f"Last reconcile report unreadable: {r['report']}")
        else:
            out.append(
                f"Last reconcile ({r['as_of']}): {r['breaks']} breaks"
                + (", kill switch armed by it" if r.get("material_mismatch") else "")
                + f"; {r['report']}"
            )
        return "\n".join(out)


def last_reconcile(profile: str, root: Path | None = None) -> dict[str, Any] | None:
    """The newest end-of-day reconcile report, summarised."""
    folder = (root or PROJECT_ROOT) / "data" / "broker" / "reconciliations"
    reports = sorted(folder.glob(f"{profile}_eod_*.json"))
    if not reports:
        return None
    try:
        doc = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"report": str(reports[-1]), "error": "unreadable"}
    return {
        "report": str(reports[-1]),
        "as_of": doc.get("as_of"),
        "generated_at": doc.get("generated_at"),
        "breaks": doc.get("diff_count"),
        "material_mismatch": doc.get("material_mismatch"),
    }


def status(
    *,
    profile: str = "paper",
    client: Any = None,
    host: str | None = None,
    port: int | None = None,
    root: Path | None = None,
) -> StatusReport:
    """The switch, the orders IBKR holds now (read-only, on the "kill" role, so a
    daily run in progress keeps its own client ID), and the last reconcile."""
    try:
        switch = read_kill_switch_state().to_dict()
    except KillSwitchCorrupted as exc:
        switch = {"armed": True, "reason": f"state file unreadable, treated as armed: {exc}"}
    report = StatusReport(switch=switch, last_reconcile=last_reconcile(profile, root))
    if client is None:
        from src.broker.ibkr.settings import make_client

        client = make_client(profile, "kill", host=host, port=port, readonly=True)
    try:
        client.connect()
    except Exception as exc:  # noqa: BLE001
        report.error = f"not reachable ({exc or type(exc).__name__})"
        return report
    try:
        try:
            client.ib.reqAllOpenOrders()
        except Exception as exc:  # noqa: BLE001
            log.warning("status: reqAllOpenOrders failed (%s)", exc)
        report.working = [_order_row(t) for t in client.open_trades()]
    except Exception as exc:  # noqa: BLE001
        report.error = f"could not read the orders ({type(exc).__name__}: {exc})"
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return report


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.execution.kill",
        description="Stop the fund (arm the kill switch and cancel every working order), resume it, or see where it stands.",
    )
    parser.add_argument("--profile", default="paper", choices=("paper", "live"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kill", help="arm the kill switch, then cancel every order IBKR still holds")
    k.add_argument("reason", help="why trading stops; it is shown wherever the switch is")
    k.add_argument("--by", help="who; defaults to the current user")
    r = sub.add_parser("resume", help="disarm the switch, after the doctor and a reconcile pass")
    r.add_argument("--by")
    sub.add_parser("status", help="the switch, IBKR's working orders and the last reconcile")
    args = parser.parse_args(argv)

    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    if args.cmd == "kill":
        report: Any = kill(args.reason, profile=args.profile, by=args.by, host=args.host, port=args.port)
        code = 0 if report.ok else 1
    elif args.cmd == "resume":
        report = resume(profile=args.profile, by=args.by, host=args.host, port=args.port)
        code = 0 if report.disarmed else 1
    else:
        report = status(profile=args.profile, host=args.host, port=args.port)
        code = 1 if report.switch.get("armed") else 0
    print(json.dumps(report.to_dict(), indent=2, default=str) if args.json else report.describe())
    return code


if __name__ == "__main__":
    sys.exit(main())
