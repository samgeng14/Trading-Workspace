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
