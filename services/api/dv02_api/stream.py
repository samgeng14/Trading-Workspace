"""Live updates over one WebSocket: /api/v1/stream.

One broadcaster task computes every topic once per tick and fans the result out
to every open socket, so ten dashboards cost what one does. A topic's ``data``
is its REST body, from the same function the REST route calls. On connect a
socket gets every topic's current value; after that, a topic only when its JSON
changes. A topic that can't be computed right now (IBKR down, no snapshot yet)
is skipped until it can; its REST route says why. The broadcaster runs only
while someone is listening.

A slow client never makes the server buffer: each socket holds at most one
unsent frame per topic, and a newer value replaces the one waiting. A topic that
hangs (IBKR not answering) is cut off after TOPIC_TIMEOUT_S so the others still go.

Which pages may open the stream (Host and Origin) is decided before the handshake
reaches here, by the app's Guard (app.py): browsers don't apply CORS to WebSockets.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import TypeAdapter
from starlette.websockets import WebSocket

log = logging.getLogger(__name__)

TICK_S = 2.0
TOPIC_TIMEOUT_S = 5.0
_JSON = TypeAdapter(Any)


def dumps(body: Any) -> str:
    """JSON exactly as the REST routes send it: FastAPI renders a route's return value
    with pydantic, which prints 0.000015 (not 1.5e-05) and NaN as null."""
    return _JSON.dump_json(body).decode()


@dataclass(eq=False)
class _Client:
    unsent: dict[str, str] = field(default_factory=dict)  # topic -> newest frame not sent yet
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    closing: bool = False


class Hub:
    def __init__(self, clock: Callable[[], str], every: float = TICK_S) -> None:
        self.topics: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.clock = clock  # the frames' `at`
        self.every = every
        self.clients: set[_Client] = set()
        self.ticks = 0
        self._last: dict[str, tuple[str, str]] = {}  # topic -> (data JSON, frame)
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        client = _Client(unsent={topic: frame for topic, (_, frame) in self._last.items()})
        if client.unsent:
            client.wake.set()
        self.clients.add(client)
        if not self.running:
            self._task = asyncio.create_task(self._run(), name="stream")
        sender = asyncio.create_task(self._send(ws, client))
        reader = asyncio.create_task(_until_closed(ws))
        try:
            await asyncio.wait({sender, reader}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.clients.discard(client)
            sender.cancel()
            reader.cancel()
        # not gather: it would re-raise our own cancels, which the server can't tell from its own
        await asyncio.wait({sender, reader})
        for t in (sender, reader):
            if not t.cancelled() and t.exception() is not None:  # a send to a socket that just went away
                log.debug("stream: client gone: %s", t.exception())

    async def stop(self) -> None:
        """Server shutdown: stop computing and close every socket."""
        for c in self.clients:
            c.closing = True
            c.wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        try:
            while self.clients:
                await self.tick()
                await asyncio.sleep(self.every)
        finally:
            self._last.clear()  # the next listener starts from fresh values

    async def tick(self) -> None:
        self.ticks += 1
        for topic, compute in self.topics.items():
            deadline = asyncio.timeout(TOPIC_TIMEOUT_S)
            try:
                async with deadline:
                    data = dumps(await compute())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — Problem or anything else; the REST route reports it
                if deadline.expired():
                    log.warning("stream: %s took over %gs; skipped this tick", topic, TOPIC_TIMEOUT_S)
                else:
                    log.debug("stream: %s skipped: %s", topic, exc)
                self._last.pop(topic, None)
                continue
            if topic in self._last and self._last[topic][0] == data:
                continue
            frame = f'{{"topic":{dumps(topic)},"at":{dumps(self.clock())},"data":{data}}}'
            self._last[topic] = (data, frame)
            for c in self.clients:
                c.unsent[topic] = frame
                c.wake.set()

    async def _send(self, ws: WebSocket, client: _Client) -> None:
        while not client.closing:
            await client.wake.wait()
            client.wake.clear()
            frames, client.unsent = client.unsent, {}
            for frame in frames.values():
                await ws.send_text(frame)
        await ws.close(1001)  # going away


async def _until_closed(ws: WebSocket) -> None:
    """The client sends nothing; reading is how a close is noticed."""
    while (await ws.receive())["type"] != "websocket.disconnect":
        pass
