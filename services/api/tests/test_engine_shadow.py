"""GET /ops/engine-shadow: the C++ engine's order manager against the Python one,
as trading-engine/src/production/engine_shadow.py records it, over the API's
engine tree."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from dv02_api import engine
from dv02_api.app import create_app


def test_no_record_yet_is_an_empty_streak(engine_tree, fake_broker, frozen_now) -> None:
    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as client:
        body = client.get("/api/v1/ops/engine-shadow").json()
    assert body["streak"] == 0 and body["needed"] == 10 and body["sessions"] == []
    assert body["last_difference"] is None and body["ready"] is False


def test_sessions_come_newest_first_and_a_difference_breaks_the_streak(engine_tree, fake_broker, frozen_now) -> None:
    from src.production import engine_shadow  # trading-engine

    def at(day: int, hour: int = 19) -> datetime:
        return datetime(2026, 10, day, hour, 35, tzinfo=timezone.utc)

    root = engine.ENGINE_ROOT
    engine_shadow.record("drift", {"differences": []}, root=root, at=at(6))
    engine_shadow.record("whatif", {"differences": [{"what": "orders, in sending order", "python": "BUY 1 SPY",
                                                     "engine": "BUY 2 SPY"}], "python_orders": [], "engine_orders": []},
                         root=root, at=at(6))  # fmt: skip
    same = {"differences": [], "python_orders": [], "engine_orders": []}
    for day in (7, 8):
        engine_shadow.record("drift", {"differences": []}, root=root, at=at(day))
        engine_shadow.record("whatif", same, root=root, at=at(day))
    engine_shadow.record("basket", {"differences": [], "python_orders": [("SPY", "BUY", 3)],
                                    "engine_orders": [("SPY", "BUY", 3)]}, root=root, at=at(8, 20))  # fmt: skip

    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as client:
        body = client.get("/api/v1/ops/engine-shadow").json()
    assert [s["session"] for s in body["sessions"]] == ["2026-10-08", "2026-10-07", "2026-10-06"]
    assert body["streak"] == 2 and body["comparisons"] == 7
    latest = body["sessions"][0]
    assert latest["match"] and latest["kinds"]["basket"]["summary"] == "same 1 orders"
    assert body["last_difference"]["session"] == "2026-10-06"
    assert body["last_difference"]["kinds"]["whatif"]["summary"].startswith("1 difference, the first: orders")
