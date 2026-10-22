"""bin/tradeit kill arms the switch and cancels every working order; resume
disarms only after the doctor and a reconcile pass; status says where things stand."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.broker.ibkr.doctor import FAIL, PASS, WARN, CheckResult  # noqa: E402
from src.execution import kill as kill_mod  # noqa: E402
from src.execution import kill_switch  # noqa: E402
from src.runtime import end_of_day_reconcile as eod  # noqa: E402


def trade(symbol: str, action: str, qty: float, *, client_id: int = 300, order_id: int = 1, status: str = "Submitted"):
    return SimpleNamespace(
        order=SimpleNamespace(clientId=client_id, orderId=order_id, permId=100 + order_id, action=action,
                              totalQuantity=qty, orderType="MOC"),
        contract=SimpleNamespace(symbol=symbol, localSymbol=symbol),
        orderStatus=SimpleNamespace(status=status, filled=0.0),
    )  # fmt: skip


class FakeIB:
    """ib_async as kill() uses it: the open orders, cancels confirmed after a few polls."""

    def __init__(self, trades: list, *, confirm_after: int | None = 2, fills_on_cancel: set[int] = frozenset()) -> None:
        """``confirm_after`` polls until IBKR confirms a cancel; None never confirms."""
        self.trades = trades
        self.confirm_after = confirm_after
        self.fills_on_cancel = fills_on_cancel
        self.cancel_calls: list[int] = []
        self.polls = 0
        self._due: dict[int, int] = {}

    def reqAllOpenOrders(self):  # noqa: N802
        return list(self.trades)

    def openTrades(self):  # noqa: N802
        return [t for t in self.trades if t.orderStatus.status not in kill_mod.CANCELLED | kill_mod.FILLED]

    def cancelOrder(self, order):  # noqa: N802
        self.cancel_calls.append(order.orderId)
        t = next(t for t in self.trades if t.order is order)
        t.orderStatus.status = "PendingCancel"
        if self.confirm_after is not None:
            self._due[order.orderId] = self.polls + self.confirm_after

    def sleep(self, _seconds: float) -> None:
        self.polls += 1
        for t in self.trades:
            due = self._due.get(t.order.orderId)
            if due is not None and self.polls >= due and t.orderStatus.status == "PendingCancel":
                t.orderStatus.status = "Filled" if t.order.orderId in self.fills_on_cancel else "Cancelled"


class FakeClient:
    def __init__(self, ib: FakeIB, *, client_id: int = 300, down: bool = False) -> None:
        self.ib = ib
        self.config = SimpleNamespace(host="127.0.0.1", port=4002, client_id=client_id, account=None)
        self.down = down
        self.connections = 0

    def connect(self) -> None:
        if self.down:
            raise ConnectionRefusedError("refused")
        self.connections += 1

    def disconnect(self) -> None:
        pass

    def open_trades(self):
        return self.ib.openTrades()

    def sleep(self, seconds: float) -> None:
        self.ib.sleep(seconds)


@pytest.fixture
def switch(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "artifacts" / "runs" / "kill_switch.json"
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", path)
    return path


# ── kill ──────────────────────────────────────────────────────────────────


def test_kill_arms_then_cancels_every_working_order(switch: Path) -> None:
    ib = FakeIB([trade("SPY", "SELL", 100, order_id=1), trade("TLT", "BUY", 400, order_id=2),
                 trade("GLD", "BUY", 10, order_id=3, client_id=307)])  # fmt: skip
    report = kill_mod.kill("ops review", by="pytest", client=FakeClient(ib))
    state = kill_switch.read_kill_switch_state()
    assert state.armed and state.reason == "ops review" and state.last_change_by == "pytest"
    assert ib.cancel_calls == [1, 2]
    assert [(r["symbol"], r["status"]) for r in report.cancelled] == [("SPY", "Cancelled"), ("TLT", "Cancelled")]
    # the dashboard's order (client 307) can only be cancelled by the dashboard
    assert [(r["symbol"], r["client_id"]) for r in report.not_ours] == [("GLD", 307)]
    assert report.ok is False and "client 307" in report.describe()
    assert "Cancelled 2 working orders: SELL 100 SPY (Cancelled), BUY 400 TLT (Cancelled)" in report.describe()


def test_kill_with_only_our_orders_is_ok(switch: Path) -> None:
    report = kill_mod.kill("ops review", client=FakeClient(FakeIB([trade("SPY", "SELL", 100)])))
    assert report.ok is True and len(report.cancelled) == 1 and report.to_dict()["ok"] is True
    report = kill_mod.kill("ops review", client=FakeClient(FakeIB([])))
    assert report.ok is True and "no working orders" in report.describe()


def test_kill_arms_even_when_ibkr_is_unreachable(switch: Path) -> None:
    report = kill_mod.kill("gateway down", client=FakeClient(FakeIB([trade("SPY", "SELL", 1)]), down=True))
    assert kill_switch.is_kill_switch_armed()
    assert report.ok is False and report.cancelled == []
    assert "client 300" in report.error and "IB Gateway or TWS" in report.error


def test_kill_reports_a_fill_that_beat_the_cancel_and_a_cancel_not_confirmed(switch: Path) -> None:
    ib = FakeIB([trade("SPY", "SELL", 100, order_id=1), trade("TLT", "BUY", 400, order_id=2)], fills_on_cancel={1})
    report = kill_mod.kill("ops review", client=FakeClient(ib))
    assert [r["symbol"] for r in report.filled] == ["SPY"] and [r["symbol"] for r in report.cancelled] == ["TLT"]
    assert report.ok is True and "Filled before the cancel" in report.describe()

    slow = FakeIB([trade("SPY", "SELL", 100)], confirm_after=None)
    report = kill_mod.kill("ops review", client=FakeClient(slow), wait=0.3)
    assert [r["status"] for r in report.pending] == ["PendingCancel"] and report.ok is False
    assert "not confirmed" in report.describe()


def test_kill_needs_a_reason(switch: Path) -> None:
    client = FakeClient(FakeIB([]))
    with pytest.raises(ValueError):
        kill_mod.kill("  ", client=client)
    assert not kill_switch.is_kill_switch_armed() and client.connections == 0


# ── resume ────────────────────────────────────────────────────────────────


def outcome(**kw) -> eod.EodReconcileResult:
    base = {"ok": True, "profile": "paper", "as_of": "2026-10-13", "diffs": [], "material_mismatch": False, "report_path": None}
    return eod.EodReconcileResult(**{**base, **kw})


def healthy() -> list[CheckResult]:
    return [CheckResult("socket", PASS, "4002 listening"), CheckResult("quotes", WARN, "delayed", "subscribe")]


def test_resume_refuses_on_a_failing_doctor(switch: Path) -> None:
    kill_switch.arm_kill_switch("ops review", by="pytest")
    broken = [CheckResult("socket", FAIL, "nothing listening on 127.0.0.1:4002", "Start IB Gateway")]
    report = kill_mod.resume(doctor=lambda: broken, reconcile=lambda: outcome())
    assert report.disarmed is False and kill_switch.is_kill_switch_armed()
    assert report.reasons == ["the doctor found socket: nothing listening on 127.0.0.1:4002"]
    assert report.doctor[0]["fix"] == "Start IB Gateway" and "Start IB Gateway" in report.describe()


def test_resume_refuses_while_the_book_is_unverified(switch: Path) -> None:
    kill_switch.arm_kill_switch("ops review", by="pytest")
    for res, text in (
        (outcome(ok=False, error="IBKR connect failed at 127.0.0.1:4002: refused"), "could not run: IBKR connect failed"),
        (outcome(skipped="the last orders fill at Tue Oct 13 16:00 ET; this reconciles them once IBKR's positions show it, from 16:30 ET"), "is waiting"),
        (outcome(diffs=[{"symbol": "TLT"}], material_mismatch=True, report_path="/r/paper_eod_2026-10-13.json"), "found 1 break(s)"),
    ):
        report = kill_mod.resume(doctor=healthy, reconcile=lambda res=res: res)
        assert report.disarmed is False and text in report.reasons[0], report.reasons
        assert kill_switch.is_kill_switch_armed()

    def boom():
        raise RuntimeError("no book")

    report = kill_mod.resume(doctor=healthy, reconcile=boom)
    assert report.disarmed is False and "the reconcile could not run: RuntimeError: no book" in report.reasons


def test_resume_disarms_when_the_doctor_and_the_reconcile_pass(switch: Path) -> None:
    kill_switch.arm_kill_switch("ops review", by="pytest")
    report = kill_mod.resume(doctor=healthy, reconcile=lambda: outcome(diffs=[{"symbol": "SPY"}]), by="owner")
    assert report.disarmed is True and report.reasons == [] and not kill_switch.is_kill_switch_armed()
    assert report.switch["last_change_by"] == "owner" and report.doctor[0]["name"] == "quotes"
    assert report.describe().startswith("Resumed")
    # nothing to reconcile yet is a pass too
    kill_switch.arm_kill_switch("ops review", by="pytest")
    report = kill_mod.resume(doctor=healthy, reconcile=lambda: outcome(skipped="no expected book yet: the first submission records one"))
    assert report.disarmed is True


# ── status ────────────────────────────────────────────────────────────────


def test_status_shows_the_switch_the_orders_and_the_last_reconcile(switch: Path, tmp_path: Path) -> None:
    kill_switch.arm_kill_switch("ops review", by="pytest")
    folder = tmp_path / "data" / "broker" / "reconciliations"
    folder.mkdir(parents=True)
    (folder / "paper_eod_2026-10-12.json").write_text(json.dumps({"as_of": "2026-10-12", "diff_count": 0, "material_mismatch": False}))
    (folder / "paper_eod_2026-10-13.json").write_text(json.dumps({"as_of": "2026-10-13", "diff_count": 1, "material_mismatch": True, "generated_at": "x"}))
    report = kill_mod.status(client=FakeClient(FakeIB([trade("SPY", "SELL", 100)])), root=tmp_path)
    assert report.switch["armed"] and report.switch["reason"] == "ops review"
    assert [r["symbol"] for r in report.working] == ["SPY"] and report.error is None
    assert report.last_reconcile["as_of"] == "2026-10-13" and report.last_reconcile["material_mismatch"] is True
    text = report.describe()
    assert "ARMED" in text and "1 working orders: SELL 100 SPY (Submitted)" in text and "1 breaks, kill switch armed by it" in text

    report = kill_mod.status(client=FakeClient(FakeIB([]), down=True), root=tmp_path / "nowhere")
    assert report.working is None and "not reachable" in report.error and report.last_reconcile is None
    assert "No reconcile report yet" in report.describe()


def test_the_cli_exit_codes(switch: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(kill_mod, "kill", lambda reason, **kw: kill_mod.KillReport(switch={"reason": reason, "last_change_by": "me"}))
    assert kill_mod.main(["kill", "ops review"]) == 0
    assert "Kill switch ARMED: ops review" in capsys.readouterr().out
    monkeypatch.setattr(kill_mod, "resume", lambda **kw: kill_mod.ResumeReport(disarmed=False, reasons=["the doctor found socket: down"]))
    assert kill_mod.main(["resume"]) == 1
    assert "Not resumed" in capsys.readouterr().out
    monkeypatch.setattr(kill_mod, "status", lambda **kw: kill_mod.StatusReport(switch={"armed": False}, working=[]))
    assert kill_mod.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["working"] == []
