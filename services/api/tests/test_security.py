"""Who may call the API. It has no login and relies on listening on 127.0.0.1, so the
checks are against the browser: a page on another site can't read the stream or send a
POST, a hostile domain resolving to 127.0.0.1 (DNS rebinding) is refused by its Host,
and the dashboard, directly or through the Vite proxy, and scripts still work."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dv02_api import ops, rebalance, runs
from dv02_api.app import create_app

from .test_contract import assert_schema, response_schema

API = "http://127.0.0.1:8000"
STREAM = "/api/v1/stream"
EVIL = "https://evil.example"
DASHBOARD_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
JSON = {"content-type": "application/json"}

# What Chrome sent through `npm run dev:api` (Vite 7's proxy, changeOrigin: false), captured
# from a real page load against a header-logging server: the browser's own Host and Origin
# reach the API unchanged and nothing is added. GETs carry no Origin; the stream and every
# POST carry the page's. The POSTs without a body (cancel, run the doctor) have no Content-Type.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.7390.37 "
    "Safari/537.36"
)
VITE_GET = {
    "host": "localhost:5173",
    "connection": "close",
    "sec-ch-ua-platform": '"Linux"',
    "user-agent": UA,
    "sec-ch-ua": '"HeadlessChrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    "sec-ch-ua-mobile": "?0",
    "accept": "*/*",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "referer": "http://localhost:5173/",
    "accept-encoding": "gzip, deflate, br, zstd",
}
VITE_POST = {**VITE_GET, "origin": "http://localhost:5173"}  # plus content-type: application/json with a body
VITE_STREAM = {
    "host": "localhost:5173",
    "pragma": "no-cache",
    "cache-control": "no-cache",
    "user-agent": UA,
    "origin": "http://localhost:5173",
    "accept-encoding": "gzip, deflate, br, zstd",
    "sec-websocket-extensions": "permessage-deflate; client_max_window_bits",
}


@pytest.fixture()
def app(engine_tree, fake_broker, frozen_now):  # noqa: ARG001
    app = create_app("paper", broker=fake_broker, background=False)
    app.state.stream.every = 0.02
    return app


@pytest.fixture()
def client(app):
    with TestClient(app, base_url=API) as c:
        yield c


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Daily runs started, with a preview that would let the submit through."""
    calls: list[str] = []
    monkeypatch.setattr(runs, "start_daily_run", lambda profile: calls.append(profile) or True)
    ready = {"checks": [], "can_submit": True, "totals": {"orders": 7}}
    monkeypatch.setattr(rebalance, "preview", lambda *a, **k: ready)
    return calls


@pytest.fixture()
def doctor_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def run(profile: str) -> dict[str, Any]:
        calls.append(profile)
        return ops.not_run_report(profile)

    monkeypatch.setattr(ops, "run_doctor", run)
    return calls


def refused_stream(c: TestClient, headers: dict[str, str]) -> int:
    with pytest.raises(WebSocketDisconnect) as refused, c.websocket_connect(STREAM, headers=headers):
        pass
    return refused.value.code


def assert_problem(res: Any, status: int, title: str) -> None:
    assert res.status_code == status, res.text
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json()["title"] == title and res.json()["detail"]


def armed(c: TestClient) -> bool:
    return c.get("/api/v1/status").json()["kill_switch"]["armed"]


# ── another site's page ────────────────────────────────────────────────


def test_a_page_on_another_site_cannot_open_the_stream(app, client) -> None:
    assert refused_stream(client, {"origin": EVIL}) == 1008  # policy violation
    assert refused_stream(client, {"origin": "null"}) == 1008  # sandboxed iframe, file:// page
    assert not app.state.stream.clients and not app.state.stream.running


def test_a_page_on_another_site_cannot_post(client, fake_broker, started, doctor_runs) -> None:
    evil = {"origin": EVIL, **JSON}
    assert_problem(client.post("/api/v1/trading/rebalance", headers=evil, json={"confirm": "SUBMIT"}), 403,
                   "Origin not allowed")  # fmt: skip
    res = client.post("/api/v1/ops/kill-switch", headers=evil, json={"armed": True, "reason": "x"})
    assert_problem(res, 403, "Origin not allowed")
    assert_problem(client.post("/api/v1/ops/doctor/run", headers={"origin": EVIL}), 403, "Origin not allowed")
    res = client.post("/api/v1/trading/orders/1002/cancel", headers={"origin": EVIL})
    assert_problem(res, 403, "Origin not allowed")
    assert started == [] and doctor_runs == [] and fake_broker.cancelled == [] and not armed(client)


def test_a_cross_site_simple_post_would_have_started_a_rebalance(client, started) -> None:
    # A form or a no-cors fetch: text/plain needs no preflight, and request.json() used to read it.
    simple = {"origin": EVIL, "content-type": "text/plain;charset=UTF-8"}
    res = client.post("/api/v1/trading/rebalance", headers=simple, content=b'{"confirm":"SUBMIT"}')
    assert_problem(res, 403, "Origin not allowed")
    assert started == []
    # the same body from the dashboard, as JSON, does start it: the checks above are what stopped it
    res = client.post("/api/v1/trading/rebalance", headers={"origin": DASHBOARD_ORIGINS[0]}, json={"confirm": "SUBMIT"})
    assert res.status_code == 200, res.text
    assert started == ["paper"]


# ── Content-Type ───────────────────────────────────────────────────────


@pytest.mark.parametrize("origin", [None, "http://localhost:5173"])
@pytest.mark.parametrize(
    "ctype", ["text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_a_body_that_is_not_json_is_refused(client, started, origin, ctype) -> None:
    headers = {"content-type": ctype} | ({"origin": origin} if origin else {})
    res = client.post("/api/v1/trading/rebalance", headers=headers, content=b'{"confirm":"SUBMIT"}')
    assert_problem(res, 415, "Send JSON")
    res = client.post("/api/v1/ops/kill-switch", headers=headers, content=b'{"armed":true,"reason":"x"}')
    assert_problem(res, 415, "Send JSON")
    assert started == [] and not armed(client)


def test_a_body_without_a_content_type_is_refused(client, started) -> None:
    res = client.post("/api/v1/trading/rebalance", content=b'{"confirm":"SUBMIT"}')
    assert "content-type" not in res.request.headers
    assert_problem(res, 415, "Send JSON")
    assert started == []


def test_json_with_a_charset_is_json(client) -> None:
    headers = {"content-type": "application/json; charset=utf-8"}
    res = client.post("/api/v1/ops/kill-switch", headers=headers, content=b'{"armed":true,"reason":"drill"}')
    assert res.status_code == 200, res.text
    assert armed(client)


def test_a_kill_switch_request_that_says_nothing_never_disarms_it(client) -> None:
    assert client.post("/api/v1/ops/kill-switch", json={"armed": True, "reason": "drill"}).status_code == 200
    assert_problem(client.post("/api/v1/ops/kill-switch"), 400, "Send a JSON object")  # no body
    assert_problem(client.post("/api/v1/ops/kill-switch", headers=JSON, content=b"not json"), 400, "Send a JSON object")
    assert_problem(client.post("/api/v1/ops/kill-switch", json=[]), 400, "Send a JSON object")
    assert armed(client)


@pytest.mark.parametrize(
    "body", [{}, {"reason": "resume"}, {"armed": "false"}, {"armed": 0}, {"armed": None}, {"armed": True, "reason": 5}]
)
def test_only_a_json_true_or_false_arms_or_disarms(client, body) -> None:
    # {} used to disarm (armed defaulted to false), and a reason that wasn't text was a 500
    assert client.post("/api/v1/ops/kill-switch", json={"armed": True, "reason": "drill"}).status_code == 200
    assert_problem(client.post("/api/v1/ops/kill-switch", json=body), 400, "Say true or false for armed")
    state = client.get("/api/v1/status").json()["kill_switch"]
    assert state["armed"] and state["reason"] == "drill"


def test_a_submit_without_a_body_asks_for_the_confirmation(client, started) -> None:
    assert client.post("/api/v1/trading/rebalance").status_code == 409
    assert client.post("/api/v1/trading/rebalance", headers=JSON, content=b"{").status_code == 409
    assert started == []


# ── Host (DNS rebinding) ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "localhost.evil.example:5173",
             "127.0.0.1:8000@evil.example", ""],
)  # fmt: skip
def test_an_unknown_host_is_refused(client, host) -> None:
    assert_problem(client.get("/api/v1/status", headers={"host": host}), 400, "Host not allowed")
    assert_problem(client.get("/docs", headers={"host": host}), 400, "Host not allowed")
    assert refused_stream(client, {"host": host}) == 1008


def test_dns_rebinding_is_refused_even_when_origin_and_host_agree(client, started) -> None:
    # evil.example resolves to 127.0.0.1: the browser treats the API as that site, so Origin
    # and Host match. Comparing the two (the stream's old check) let it read the account.
    rebound = {"host": "evil.example:8000", "origin": "http://evil.example:8000"}
    assert refused_stream(client, rebound) == 1008
    res = client.post("/api/v1/trading/rebalance", headers=rebound, json={"confirm": "SUBMIT"})
    assert_problem(res, 400, "Host not allowed")
    assert started == []


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST:5173", "localhost", "[::1]:8000"])
def test_loopback_hosts_are_served(client, host) -> None:
    assert client.get("/api/v1/status", headers={"host": host}).status_code == 200


# ── who may call ───────────────────────────────────────────────────────


def test_scripts_send_no_origin_and_may_do_everything(client, fake_broker, started, doctor_runs) -> None:
    # curl, Python, bin/tradeit
    with client.websocket_connect(STREAM) as ws:
        assert ws.receive_json()["topic"]
    assert client.post("/api/v1/trading/orders/1002/cancel").status_code == 200  # before the arm cancels it
    assert client.post("/api/v1/ops/kill-switch", json={"armed": True, "reason": "drill"}).status_code == 200
    assert client.post("/api/v1/ops/kill-switch", json={"armed": False}).status_code == 200
    assert client.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"}).status_code == 200
    assert client.post("/api/v1/ops/doctor/run").status_code == 200
    assert started == ["paper"] and doctor_runs == ["paper"] and fake_broker.cancelled == ["1002"]


@pytest.mark.parametrize("origin", DASHBOARD_ORIGINS)
def test_the_dashboards_origins_may_listen_and_post(client, origin) -> None:
    with client.websocket_connect(STREAM, headers={"origin": origin}) as ws:
        assert ws.receive_json()["topic"]
    res = client.post("/api/v1/ops/kill-switch", headers={"origin": origin}, json={"armed": True, "reason": "drill"})
    assert res.status_code == 200, res.text
    assert armed(client)


def test_a_page_the_api_or_its_proxy_served_may_call_on_any_port(client) -> None:
    # vite preview (4173), or the API on DV02_API_PORT: the page's origin is the request's own host
    for port in (4173, 8100):
        own = {"host": f"localhost:{port}", "origin": f"http://localhost:{port}"}
        with client.websocket_connect(STREAM, headers=own) as ws:
            assert ws.receive_json()["topic"]
        assert client.post("/api/v1/ops/kill-switch", headers=own, json={"armed": False}).status_code == 200
    # another local server's page is still another site
    assert refused_stream(client, {"host": "localhost:4173", "origin": "http://localhost:3000"}) == 1008


def test_the_dashboards_requests_through_the_vite_proxy_are_accepted(client, app, fake_broker, doctor_runs) -> None:
    for path in ("/status", "/market/strip", "/portfolio/summary", "/portfolio/positions", "/portfolio/intraday",
                 "/risk", "/trading/orders", "/trading/rebalance/preview", "/ops/doctor", "/ops/runs"):  # fmt: skip
        res = client.get(f"/api/v1{path}", headers=VITE_GET)
        assert res.status_code == 200, (path, res.text)
        assert_schema(res.json(), response_schema(path, "get"))
    with client.websocket_connect(STREAM, headers=VITE_STREAM) as ws:
        assert ws.receive_json()["topic"]
        assert len(app.state.stream.clients) == 1
    # no body: content-length 0 and no Content-Type, as the browser sent them
    res = client.post("/api/v1/ops/doctor/run", headers=VITE_POST)
    assert res.request.headers["content-length"] == "0" and "content-type" not in res.request.headers
    assert res.status_code == 200, res.text
    res = client.post("/api/v1/trading/orders/1002/cancel", headers=VITE_POST)
    assert res.status_code == 200, res.text
    res = client.post("/api/v1/ops/kill-switch", headers=VITE_POST | JSON, json={"armed": True, "reason": "drill"})
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema("/ops/kill-switch", "post"))
    assert doctor_runs == ["paper"] and fake_broker.cancelled == ["1002"]


def test_more_hosts_and_origins_from_the_environment(engine_tree, fake_broker, frozen_now, monkeypatch) -> None:  # noqa: ARG001
    monkeypatch.setenv("DV02_ALLOWED_HOSTS", "dv02.lan, [fe80::1]")
    monkeypatch.setenv("DV02_ALLOWED_ORIGINS", "https://desk.dv02.lan/")
    with TestClient(create_app("paper", broker=fake_broker, background=False), base_url=API) as c:
        for host in ("dv02.lan:8000", "[fe80::1]:8000"):
            assert c.get("/api/v1/status", headers={"host": host}).status_code == 200
        with c.websocket_connect(STREAM, headers={"origin": "https://desk.dv02.lan"}) as ws:
            assert ws.receive_json()["topic"]
        assert refused_stream(c, {"origin": EVIL}) == 1008
        assert_problem(c.get("/api/v1/status", headers={"host": "evil.example"}), 400, "Host not allowed")
