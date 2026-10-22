"""The /stream WebSocket: frames match the contract and the REST bodies, one computation
serves every client, and after the first batch only changes go out."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dv02_api import stream
from dv02_api.app import create_app

from .test_contract import SPEC, assert_schema

URL = "/api/v1/stream"
REST = {
    "status": "/status",
    "strip": "/market/strip",
    "summary": "/portfolio/summary",
    "positions": "/portfolio/positions",
    "intraday": "/portfolio/intraday",
    "orders": "/trading/orders",
}
TOPICS = set(REST)
FRAME = SPEC["components"]["schemas"]["StreamMessage"]


@pytest.fixture()
def app(engine_tree, fake_broker, frozen_now):  # noqa: ARG001
    app = create_app("paper", broker=fake_broker, background=False)
    app.state.stream.every = 0.02
    return app


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


def first_batch(ws: Any, n: int = len(TOPICS)) -> dict[str, dict[str, Any]]:
    frames = [ws.receive_json() for _ in range(n)]
    return {f["topic"]: f for f in frames}


def test_first_frames_cover_every_topic_and_are_the_rest_bodies(app) -> None:
    with TestClient(app) as c, c.websocket_connect(URL) as ws:
        raw = [ws.receive_text() for _ in TOPICS]
        frames = [json.loads(r) for r in raw]
        assert {f["topic"] for f in frames} == TOPICS
        for text, frame in zip(raw, frames, strict=True):
            assert_schema(frame, FRAME)
            assert frame["at"] == "2026-10-09T18:12:00+00:00"
            rest = c.get(f"/api/v1{REST[frame['topic']]}")
            assert text.endswith(f',"data":{rest.text}}}'), frame["topic"]  # byte for byte


def test_frames_keep_the_rest_bytes_for_small_numbers_and_nan(app, fake_broker) -> None:
    # REST prints a 0.0015% move as 0.000015 and a NaN as null; Python's json would
    # print 1.5e-05 and refuse the NaN, dropping the topic
    quotes = fake_broker.quotes()
    quotes[0]["change_pct"] = 1.5e-05
    quotes[1]["change"] = float("nan")
    fake_broker.quotes = lambda: quotes
    with TestClient(app) as c, c.websocket_connect(URL) as ws:
        raw = {json.loads(t)["topic"]: t for t in [ws.receive_text() for _ in TOPICS]}
        rest = c.get("/api/v1/market/strip")
        assert '"change_pct":0.000015' in rest.text and '"change":null' in rest.text
        assert raw["strip"].endswith(f',"data":{rest.text}}}')


def test_intraday_ends_with_the_live_pnl_while_open(app, frozen_now) -> None:
    with TestClient(app) as c, c.websocket_connect(URL) as ws:
        tip = first_batch(ws)["intraday"]["data"]["points"][-1]
        assert tip == {"t": frozen_now.isoformat(), "pnl": 2_950.0}
        assert c.get("/api/v1/portfolio/intraday").json()["points"][-1] == tip


def test_intraday_tip_replaces_a_sample_in_the_same_second(app, fake_broker, monkeypatch) -> None:
    from dv02_api import app as app_mod

    now = datetime(2026, 10, 9, 18, 12, 0, 700_000, tzinfo=timezone.utc)
    monkeypatch.setattr(app_mod, "_now", lambda: now)
    samples = [(now - timedelta(seconds=15), 2_900.0), (now.replace(microsecond=300_000), 2_940.0)]
    fake_broker.intraday = lambda d: samples
    with TestClient(app) as c:
        points = c.get("/api/v1/portfolio/intraday").json()["points"]
    assert points == [
        {"t": "2026-10-09T18:11:45+00:00", "pnl": 2_900.0},
        {"t": "2026-10-09T18:12:00+00:00", "pnl": 2_950.0},  # the live value, once
    ]


def test_intraday_has_no_live_tip_outside_the_session(app, fake_broker, monkeypatch) -> None:
    from dv02_api import app as app_mod

    monkeypatch.setattr(app_mod, "_now", lambda: datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc))  # 17:00 ET
    with TestClient(app) as c:
        body = c.get("/api/v1/portfolio/intraday").json()
    assert body["live"] is False
    assert [p["pnl"] for p in body["points"]] == [v for _, v in fake_broker.intraday(None)]


def test_a_change_is_sent_and_nothing_else(app, fake_broker) -> None:
    hub = app.state.stream
    with TestClient(app) as c, c.websocket_connect(URL) as ws:
        first_batch(ws)
        start = hub.ticks
        wait_until(lambda: hub.ticks >= start + 3)  # nothing changed for a few ticks...
        quotes = fake_broker.quotes()
        quotes[0]["last"] = 6_660.0
        fake_broker.quotes = lambda: quotes
        frame = ws.receive_json()  # ...so the first frame since is the change, not a repeat
        assert frame["topic"] == "strip"
        assert frame["data"]["items"][0]["last"] == 6_660.0
        assert_schema(frame, FRAME)
        assert frame["data"] == c.get("/api/v1/market/strip").json()


def test_a_failing_topic_is_skipped_and_comes_back(app, fake_broker, engine_tree) -> None:
    def feed_down() -> list[dict[str, Any]]:
        raise RuntimeError("quote feed down")

    # strip raises a plain error; summary and positions a Problem (no account at all)
    fake_broker.quotes = feed_down
    fake_broker.snapshot = lambda: None
    for f in (engine_tree / "data" / "broker" / "account").iterdir():
        f.unlink()
    hub = app.state.stream
    with TestClient(app) as c, c.websocket_connect(URL) as ws:
        assert c.get("/api/v1/portfolio/summary").status_code == 503
        assert set(first_batch(ws, 3)) == {"status", "intraday", "orders"}
        start = hub.ticks
        wait_until(lambda: hub.ticks >= start + 3)
        assert hub.running
        del fake_broker.quotes, fake_broker.snapshot  # back to the fake's own methods
        # intraday changes too: it now ends with the live P&L
        back = first_batch(ws, 4)
        assert set(back) == {"strip", "summary", "positions", "intraday"}
        for frame in back.values():
            assert_schema(frame, FRAME)


def test_two_clients_share_one_computation(app, fake_broker) -> None:
    hub = app.state.stream
    calls = []
    quotes = fake_broker.quotes
    fake_broker.quotes = lambda: calls.append(1) or quotes()
    with TestClient(app) as c:
        with c.websocket_connect(URL) as a, c.websocket_connect(URL) as b:
            assert set(first_batch(a)) == TOPICS
            assert set(first_batch(b)) == TOPICS
            start = hub.ticks
            wait_until(lambda: hub.ticks >= start + 5)
            assert len(hub.clients) == 2
        wait_until(lambda: not hub.running)
        assert hub.ticks >= 5 and len(calls) == hub.ticks  # once per tick, not once per client


def test_disconnect_cleans_up_and_a_later_client_starts_fresh(app) -> None:
    hub = app.state.stream
    with TestClient(app) as c:
        with c.websocket_connect(URL) as ws:
            first_batch(ws)
            assert len(hub.clients) == 1 and hub.running
        wait_until(lambda: not hub.clients and not hub.running)
        with c.websocket_connect(URL) as ws:
            assert set(first_batch(ws)) == TOPICS


def test_a_page_on_another_site_is_refused(app) -> None:
    # browsers don't apply CORS to WebSockets, so any open web page could otherwise read the account
    with TestClient(app) as c:
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            c.websocket_connect(URL, headers={"origin": "https://evil.example"}),
        ):
            pass
        assert refused.value.code == 1008
        assert not app.state.stream.clients
        with c.websocket_connect(URL, headers={"origin": "http://testserver"}) as ws:  # the dashboard's own page
            assert set(first_batch(ws)) == TOPICS


def test_shutdown_closes_open_sockets(app) -> None:
    hub = app.state.stream
    with TestClient(app) as c, c.websocket_connect(URL) as ws:
        first_batch(ws)
        c.portal.call(hub.stop)
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 1001
        assert not hub.running


class SlowSocket:
    """A client that takes a frame only when the test lets it."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}  # not a browser: no origin
        self.sent: list[dict[str, Any]] = []
        self.blocked = False
        self.gate = asyncio.Event()
        self.gone = asyncio.Event()

    async def accept(self) -> None: ...

    async def send_text(self, text: str) -> None:
        self.blocked = True
        await self.gate.wait()
        self.sent.append(json.loads(text))

    async def receive(self) -> dict[str, Any]:
        await self.gone.wait()
        return {"type": "websocket.disconnect"}

    async def close(self, code: int = 1000) -> None: ...


def test_a_slow_client_gets_the_newest_value_not_a_backlog() -> None:
    async def scenario() -> list[int]:
        n = 0

        async def counter() -> dict[str, int]:
            return {"n": n}

        hub = stream.Hub(clock=lambda: "2026-10-09T18:12:00+00:00", every=3600)
        hub.topics = {"strip": counter}
        ws = SlowSocket()
        serving = asyncio.create_task(hub.serve(ws))  # type: ignore[arg-type]
        while not ws.blocked:  # the first frame is stuck in the client's socket
            await asyncio.sleep(0)
        for n in (1, 2, 3):  # noqa: B007 — counter() reads it
            await hub.tick()
        ws.gate.set()
        while len(ws.sent) < 2:
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        ws.gone.set()
        await serving
        await hub.stop()
        return [f["data"]["n"] for f in ws.sent]

    assert asyncio.run(scenario()) == [0, 3]
