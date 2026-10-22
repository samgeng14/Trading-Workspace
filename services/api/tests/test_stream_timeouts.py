"""A topic that hangs (IBKR not answering) doesn't hold up the stream's other topics."""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from dv02_api import stream
from dv02_api.app import create_app


def test_a_hung_topic_is_cut_off_and_the_rest_still_go(engine_tree, fake_broker, frozen_now, monkeypatch) -> None:  # noqa: ARG001
    monkeypatch.setattr(stream, "TOPIC_TIMEOUT_S", 0.05)
    app = create_app("paper", broker=fake_broker, background=False)
    hub = app.state.stream
    hub.every = 0.02
    cancelled: list[bool] = []

    async def hangs() -> dict:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return {}

    hub.topics["orders"] = hangs
    with TestClient(app) as c, c.websocket_connect("/api/v1/stream") as ws:
        topics = {ws.receive_json()["topic"] for _ in range(5)}
        assert topics == {"status", "strip", "summary", "positions", "intraday"}
        start, deadline = hub.ticks, time.monotonic() + 5
        while hub.ticks < start + 3:  # still ticking, a hung call cut off each time
            assert time.monotonic() < deadline, "the stream stalled on the hung topic"
            time.sleep(0.01)
    assert cancelled and "orders" not in hub._last
