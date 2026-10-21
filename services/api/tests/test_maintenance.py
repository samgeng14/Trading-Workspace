"""The API's upkeep loop: a slow Flex history walk runs beside it, never in
front of the closing NAV."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from dv02_api import app as app_mod
from dv02_api import flex, history, ops

from .conftest import FakeBroker


def test_a_slow_flex_walk_never_holds_up_the_closing_nav(monkeypatch) -> None:
    release = threading.Event()
    walks: list[str] = []
    closes: list[tuple] = []

    def slow_refresh(profile: str) -> str:  # a full walk under IBKR's pacing: minutes
        walks.append(profile)
        release.wait(5)
        return "done"

    after_close = datetime(2026, 10, 15, 20, 30, tzinfo=timezone.utc)  # 16:30 ET, a Thursday
    monkeypatch.setattr(app_mod, "_now", lambda: after_close)
    monkeypatch.setattr(app_mod, "MAINTENANCE_INTERVAL", 0.01)
    monkeypatch.setattr(flex, "refresh", slow_refresh)
    monkeypatch.setattr(history, "record_close_nav", lambda *a: closes.append(a))
    monkeypatch.setattr(history, "bars_stale", lambda *a: False)
    monkeypatch.setattr(ops, "last_doctor", lambda profile: {"ran_at": after_close.isoformat()})
    ctx = app_mod.Ctx(profile="paper", broker=FakeBroker())

    async def scenario() -> asyncio.Task:
        loop = asyncio.create_task(app_mod._maintenance(ctx))
        await asyncio.sleep(0.2)  # ~20 passes while the walk is still running
        assert walks == ["paper"]  # one walk, never a second beside it
        assert len(closes) >= 5  # every pass recorded the close meanwhile
        flex_task = next(t for t in asyncio.all_tasks() if t.get_name() == "flex-refresh")
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        await asyncio.sleep(0)
        release.set()  # let the worker thread finish
        return flex_task

    flex_task = asyncio.run(scenario())
    assert flex_task.cancelled()  # stopping the API stops waiting on the walk
    assert closes[0][:2] == ("paper", after_close.date())


def _passes(monkeypatch, now: datetime) -> list[tuple]:
    """A few upkeep passes at ``now``; the closes they recorded."""
    closes: list[tuple] = []
    monkeypatch.setattr(app_mod, "_now", lambda: now)
    monkeypatch.setattr(app_mod, "MAINTENANCE_INTERVAL", 0.01)
    monkeypatch.setattr(flex, "refresh", lambda profile: "done")
    monkeypatch.setattr(history, "record_close_nav", lambda *a: closes.append(a))
    monkeypatch.setattr(history, "bars_stale", lambda *a: False)
    monkeypatch.setattr(ops, "last_doctor", lambda profile: {"ran_at": now.isoformat()})
    ctx = app_mod.Ctx(profile="paper", broker=FakeBroker())

    async def scenario() -> None:
        loop = asyncio.create_task(app_mod._maintenance(ctx))
        await asyncio.sleep(0.1)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)

    asyncio.run(scenario())
    return closes


def test_the_close_is_recorded_once_the_closing_prints_settle(monkeypatch) -> None:
    assert _passes(monkeypatch, datetime(2026, 10, 15, 20, 5, tzinfo=timezone.utc)) == []  # 16:05 ET
    closes = _passes(monkeypatch, datetime(2026, 10, 15, 20, 16, tzinfo=timezone.utc))  # 16:16 ET
    assert closes and closes[0][:2] == ("paper", datetime(2026, 10, 15).date())
    # an early close (the day after Thanksgiving, 13:00 ET): settled by 13:15
    assert _passes(monkeypatch, datetime(2026, 11, 27, 18, 16, tzinfo=timezone.utc))  # 13:16 ET


def test_the_first_settled_close_is_kept(engine_tree) -> None:
    day = datetime(2026, 10, 15).date()
    history.record_close_nav("paper", day, 1_048_000.00)
    history.record_close_nav("paper", day, 1_049_250.00)  # 23:00 ET, after-hours marks
    assert history.own_navs("paper")[day] == 1_048_000.00


def test_the_doctor_keeps_the_days_report(engine_tree, monkeypatch) -> None:
    from src.broker.ibkr import doctor

    from dv02_api import engine

    monkeypatch.setattr(doctor, "run_doctor", lambda profile: [doctor.CheckResult("socket", doctor.PASS, "ok")])
    report = ops._run_doctor_blocking("paper")
    day = datetime.fromisoformat(report["ran_at"]).astimezone(doctor.NEW_YORK).date()
    assert ops.last_doctor("paper") == report
    kept = engine.artifacts_dir() / "doctor" / f"doctor_paper_{day.isoformat()}.json"
    assert kept.exists() and '"status": "PASS"' in kept.read_text()
