"""GET /trading/today: did the fund trade today, in one plain answer; and the order fixes it
needs (IBKR's completed orders, the reconcile's fills when IBKR isn't connected)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dv02_api import app as app_mod
from dv02_api import nyse, orders, runs, today
from dv02_api.app import create_app
from dv02_api.broker import Broker

from .conftest import FakeBroker, fake_trade
from .test_contract import assert_schema, response_schema

# Friday Oct 16 2026, 14:35 ET: the after-hours run sent its orders for today's open at 00:35 Central
OCT16_AFTERNOON = datetime(2026, 10, 16, 18, 35, tzinfo=timezone.utc)
RUN_ID = "20261016_003532"
PERMS = ["199060002", "199060003", "199060004"]


def write_open_run(root: Path, *, perms: list[str] = PERMS) -> None:
    """The Oct 16 run: three market-on-open orders for the Oct 16 session, as the runner logs them."""
    sub_name = "paper_orders_submitted_20261016_003539.csv"
    orders_dir = root / "data" / "broker" / "orders"
    orders_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        "timestamp,symbol,action,order_type,tif,quantity,delta_shares,estimated_trade_dollars,status,order_id,perm_id"
    ]
    for i, (perm, sym) in enumerate(zip(perms, ["GLD", "DBC", "SLV"], strict=False)):
        rows.append(f"2026-10-16T00:35:40,{sym},SELL,MKT,OPG,{100 + i},-{100 + i},1000,PreSubmitted,{4 + i},{perm}")
    (orders_dir / sub_name).write_text("\n".join(rows) + "\n")
    doc = {
        "run_id": RUN_ID,
        "date": "2026-10-16",
        "profile": "paper",
        "dry_run": False,
        "should_rebalance": True,
        "rebalance_decision": {"rebalance": True, "reason": "drift above 5%"},
        "ok": True,
        "steps": {
            "submit_orders": {
                "ok": True,
                "submitted": True,
                # the path on the machine that ran it: the API finds the file by name
                "submission_path": f"/Users/someone/elsewhere/{sub_name}",
                "result": {
                    "submitted": True,
                    "rows": len(perms),
                    "execution": {
                        "order_type": "MKT",
                        "tif": "OPG",
                        "auction": "open",
                        "session": "2026-10-16",
                        "fills_at": "2026-10-16T09:30:00-04:00",
                        "summary": "market-on-open, filled at the Fri Oct 16 open",
                    },
                },
            }
        },
    }
    (root / "artifacts" / "runs" / f"daily_run_{RUN_ID}.json").write_text(json.dumps(doc))


def write_reconcile(root: Path, filled: dict[str, float]) -> None:
    folder = root / "data" / "broker" / "reconciliations"
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "perm_id": perm,
            "quantity": -100.0,
            "filled": -qty,
            "price": 380.0,
            "fills_at": "2026-10-16T09:30:00-04:00",
            "counted": "at the fills IBKR reports",
            "run_id": RUN_ID,
        }
        for perm, qty in filled.items()
    ]
    (folder / "paper_eod_2026-10-16.json").write_text(json.dumps({"as_of": "2026-10-16", "orders": rows}))


def sent_at() -> str:
    """How the answer prints the run's start: the run ID is the runner's local time, read the same way."""
    at = runs._run_start(RUN_ID)
    assert at is not None
    return f"{at.astimezone(nyse.ET):%H:%M} ET"


def filled_trade(perm: str, status: str = "Filled") -> Any:
    return fake_trade(int(perm), "GLD", "SELL", 100, status, filled=100 if status == "Filled" else 0)


def answer(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "profile": "paper",
        "now": OCT16_AFTERNOON,
        "connected": True,
        "live_orders": [],
        "snapshot_at": None,
        "kill_armed": False,
    }
    args.update(kw)
    return today.trading_day(**args)


@pytest.fixture(autouse=True)
def no_dashboard_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runs,
        "RUN_STATE",
        {"running": False, "started_at": None, "started_iso": None, "result": None, "log": None, "progress": None},
    )


# ── the answer ─────────────────────────────────────────────────────────


def test_before_the_run_it_says_when_the_run_is_and_offers_the_button(engine_tree) -> None:
    doc = answer(now=datetime(2026, 10, 9, 18, 12, tzinfo=timezone.utc))  # Fri 14:12 ET
    assert doc["state"] == "not_yet"
    assert doc["headline"] == "Not traded yet today"
    assert doc["detail"].startswith("The automatic run is at 15:35 ET: market-on-close")
    assert doc["next_run_at"] == "2026-10-09T15:35:00-04:00"
    assert doc["can_rebalance"] is True and "blocked_reason" not in doc
    assert doc["steps"] == []


def test_traded_at_the_open_with_every_fill_from_ibkr(engine_tree) -> None:
    write_open_run(engine_tree)
    live = [orders.from_trade(filled_trade(p), "ETF") for p in PERMS]
    doc = answer(live_orders=live)
    assert doc["state"] == "done"
    assert doc["headline"] == f"Traded at today's open (orders sent {sent_at()})"
    assert doc["detail"] == "Filled 3/3 at the 09:30 ET open."
    assert doc["orders"] == {
        "sent": 3,
        "filled": 3,
        "working": 0,
        "cancelled": 0,
        "auction": "open",
        "fills_at": "2026-10-16T09:30:00-04:00",
        "source": "ibkr",
    }
    # nothing more to do today, and nothing red: the button explains itself
    assert doc["can_rebalance"] is False and doc["blocked_reason"] == "This session's orders are already in."


def test_offline_the_fills_come_from_the_reconcile(engine_tree, monkeypatch: pytest.MonkeyPatch) -> None:
    write_open_run(engine_tree)
    write_reconcile(engine_tree, {PERMS[0]: 100, PERMS[1]: 100})  # the third isn't in the report
    monkeypatch.setenv("DV02_TRADING_HOST", "the VM")
    snap = datetime(2026, 10, 16, 18, 5, tzinfo=timezone.utc)
    doc = answer(connected=False, live_orders=None, snapshot_at=snap)
    assert doc["state"] == "done" and doc["orders"]["source"] == "reconcile"
    assert doc["detail"] == "Filled 2/3; 1 working."
    assert doc["connected"] is False and doc["snapshot_at"] == "2026-10-16T18:05:00+00:00"
    assert doc["note"] == (
        "IBKR isn't connected on this machine, so the pages show the snapshot from 14:05 ET. Trading runs on the VM."
    )


def test_offline_with_no_reconcile_it_only_says_they_were_sent(engine_tree, monkeypatch) -> None:
    write_open_run(engine_tree)
    monkeypatch.delenv("DV02_TRADING_HOST", raising=False)
    doc = answer(connected=False, live_orders=None)
    assert doc["orders"]["source"] == "submission"
    assert doc["detail"] == (
        "3 orders sent; the fills show once IBKR is connected here or the end-of-day reconcile runs."
    )
    assert doc["note"].endswith("Trading runs on the machine that holds the IBKR gateway.")


def test_a_run_that_found_nothing_to_trade_is_a_hold(engine_tree) -> None:
    doc = {
        "run_id": "20261009_143500",
        "date": "2026-10-09",
        "dry_run": False,
        "ok": True,
        "should_rebalance": False,
        "rebalance_decision": {"rebalance": False, "reason": "max drift 2.1% under 5%"},
        "steps": {},
    }
    (engine_tree / "artifacts" / "runs" / "daily_run_20261009_143500.json").write_text(json.dumps(doc))
    out = answer(now=datetime(2026, 10, 9, 20, 30, tzinfo=timezone.utc))
    assert out["state"] == "held" and out["headline"] == "No trade needed today"
    assert out["detail"].endswith("run checked the book: max drift 2.1% under 5%.")
    assert out["can_rebalance"] is True


def test_a_run_that_stopped_says_why_in_one_sentence(engine_tree) -> None:
    doc = {
        "run_id": "20261009_143500",
        "date": "2026-10-09",
        "dry_run": False,
        "ok": False,
        "error": "append_daily failed",
        "steps": {"append_daily": {"ok": False, "error": "IBKR connection refused at 127.0.0.1:4002"}},
    }
    (engine_tree / "artifacts" / "runs" / "daily_run_20261009_143500.json").write_text(json.dumps(doc))
    out = answer(now=datetime(2026, 10, 9, 20, 30, tzinfo=timezone.utc))
    assert out["state"] == "failed" and out["headline"] == "Not traded today: the run stopped"
    assert out["detail"].endswith(
        "stopped before sending orders: etf bars failed: IBKR connection refused at 127.0.0.1:4002."
    )


def test_a_rebalance_started_from_the_dashboard_shows_its_steps(engine_tree, monkeypatch) -> None:
    progress = engine_tree / "artifacts" / "api" / "dashboard_run_20261009_141500.progress.json"
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(
        json.dumps(
            {
                "run_id": "20261009_141500",
                "date": "2026-10-09",
                "dry_run": False,
                "current": "build_basket",
                "finished": False,
                "should_rebalance": True,
                "rebalance_decision": {"rebalance": True, "reason": "forced from the dashboard"},
                "steps": {
                    "reconcile": {"ok": True, "action": "reconcile", "diffs": []},
                    "append_daily": {"ok": True, "new_rows_added_to_master": 21, "latest_date": "2026-10-08"},
                    "generate_targets": {
                        "ok": True,
                        "optimizer": "momentum_tilted",
                        "latest_rebalance_date": "2026-10-08",
                    },
                },
            }
        )
    )
    runs.RUN_STATE.update(running=True, started_iso="2026-10-09T14:15:00-04:00", progress=str(progress))
    doc = answer(now=datetime(2026, 10, 9, 18, 16, tzinfo=timezone.utc))
    assert doc["state"] == "running"
    assert doc["headline"] == "Rebalancing now: building the basket"
    assert [(s["id"], s["state"]) for s in doc["steps"]] == [
        ("reconcile", "done"),
        ("append_daily", "done"),
        ("generate_targets", "done"),
        ("rebalance_decision", "done"),
        ("build_basket", "running"),
        ("submit_orders", "pending"),
    ]
    assert doc["steps"][3]["detail"] == "rebalance: forced from the dashboard"
    assert doc["can_rebalance"] is False and doc["blocked_reason"] == "A run is already working."


def test_on_a_closed_day_it_names_the_next_run(engine_tree) -> None:
    doc = answer(now=datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc))  # Saturday
    assert doc["state"] == "closed" and doc["trading_date"] == "2026-10-12"
    assert doc["detail"] == "The next automatic run is Mon 15:35 ET."
    assert doc["can_rebalance"] is False


def test_the_button_says_why_it_is_off(engine_tree) -> None:
    friday = datetime(2026, 10, 9, 18, 12, tzinfo=timezone.utc)
    assert answer(now=friday, connected=False)["blocked_reason"] == "IBKR isn't connected on this machine."
    assert answer(now=friday, kill_armed=True)["blocked_reason"] == "The kill switch is armed: resume trading first."


def test_the_endpoint_matches_the_contract(engine_tree, frozen_now) -> None:  # noqa: ARG001
    with TestClient(create_app("paper", broker=FakeBroker(), background=False)) as c:
        res = c.get("/api/v1/trading/today")
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema("/trading/today", "get"))
    assert res.json()["state"] == "not_yet"


def test_the_endpoint_offline_matches_the_contract(engine_tree, monkeypatch) -> None:
    write_open_run(engine_tree)
    monkeypatch.setattr(app_mod, "_now", lambda: OCT16_AFTERNOON)
    with TestClient(create_app("paper", broker=FakeBroker(connected=False), background=False)) as c:
        res = c.get("/api/v1/trading/today")
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema("/trading/today", "get"))
    assert res.json()["state"] == "done" and res.json()["connected"] is False


# ── the order fixes underneath ─────────────────────────────────────────


class FakeIB:
    """ib_async as the API sees it after the runner's market-on-open orders filled: the trade
    it saw while the order was open still says Submitted; IBKR's completed orders say Filled."""

    def __init__(self) -> None:
        self.open = fake_trade(199060002, "GLD", "SELL", 180, "Submitted")
        done = fake_trade(199060002, "GLD", "SELL", 180, "Filled")
        done.order.filledQuantity = 180.0
        done.orderStatus.filled = 0.0  # a completed order's status carries no fill
        done.log = []
        self.done = [done, fake_trade(199060009, "SLV", "SELL", 10, "Cancelled")]

    def isConnected(self) -> bool:  # noqa: N802
        return True

    def trades(self) -> list[Any]:
        return [self.open]

    async def reqAllOpenOrdersAsync(self) -> list[Any]:  # noqa: N802
        return [self.open]

    async def reqCompletedOrdersAsync(self, api_only: bool) -> list[Any]:  # noqa: N802, ARG002
        return self.done


def test_completed_orders_replace_the_stale_open_status(engine_tree) -> None:  # noqa: ARG001
    b = Broker("paper")
    b._ib = FakeIB()
    b.status.connected = True
    before = [orders.from_trade(t, "ETF") for t in b.trades()]
    assert before[0]["status"] == "submitted" and before[0]["filled"] == 0
    asyncio.run(b.refresh_orders())
    after = {o["id"]: o for o in (orders.from_trade(t, "ETF") for t in b.trades())}
    assert after["199060002"]["status"] == "filled" and after["199060002"]["filled"] == 180
    # the submit time is still the one seen while the order was open
    assert after["199060002"]["submitted_at"] == before[0]["submitted_at"]
    assert after["199060009"]["status"] == "cancelled"


def test_an_unset_fill_reads_as_none(engine_tree) -> None:  # noqa: ARG001
    t = fake_trade(5, "GLD", "BUY", 10, "Submitted")
    t.order.filledQuantity = 1.7976931348623157e308  # ib_async's UNSET_DOUBLE
    assert orders.from_trade(t, "ETF")["filled"] == 0


def test_the_offline_blotter_takes_the_reconciles_fills(engine_tree) -> None:
    write_open_run(engine_tree)
    write_reconcile(engine_tree, {PERMS[0]: 100})
    fills = engine_tree / "data" / "broker" / "fills"
    fills.mkdir(parents=True, exist_ok=True)
    (fills / "paper_trade_log_20261016_003539.csv").write_text(
        "timestamp,symbol,order_id,perm_id,action,order_type,quantity,status,filled_qty,avg_fill_price\n"
        f"2026-10-16T00:35:40,GLD,4,{PERMS[0]},SELL,MKT,100.0,PreSubmitted,0,\n"
    )
    day, rows = orders.from_files("paper", "ETF")
    by = {r["id"]: r for r in rows}
    assert day == "2026-10-16"
    assert by[PERMS[0]]["status"] == "filled" and by[PERMS[0]]["filled"] == 100
    assert by[PERMS[0]]["avg_fill_price"] == 380.0
    assert by[PERMS[1]]["status"] == "submitted" and by[PERMS[1]]["filled"] == 0


def test_a_dashboard_run_writes_a_progress_file(engine_tree) -> None:  # noqa: ARG001
    cmd, _ = runs.runner_command("paper", Path("/tmp/x.progress.json"))
    assert cmd[-2:] == ["--progress-file", "/tmp/x.progress.json"]
    assert "--force-rebalance" in cmd
    cmd, _ = runs.runner_command("paper")
    assert "--progress-file" not in cmd
