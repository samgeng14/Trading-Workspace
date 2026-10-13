"""The staging harness (python -m dv02_api.staging): each scenario has the paper account's
real shape, and every REST endpoint answers contract-valid JSON over it."""

from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dv02_api import engine, staging
from dv02_api.universe import MODEL

from .test_contract import GETS, SPEC, assert_schema, response_schema

NOW = staging.parse_now("2026-10-12T14:30:00")  # Monday, inside the session, after 16:32 UTC
PROBLEM = SPEC["components"]["schemas"]["Problem"]
OUTSIDE = {"DBC", "XLE", "XLP", "XLU", "TIP", "EWY", "SOXX", "EFA", "IWM"}


@pytest.fixture()
def utc():
    """Staging runs on UTC (the daily run's 16:32 is local time); restore the zone after."""
    old = os.environ.get("TZ")
    staging.pin_timezone("UTC")
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _client(scenario: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return TestClient(staging.create_staging_app(scenario, root, NOW, monkeypatch))


@pytest.fixture(params=staging.SCENARIOS)
def client(request, tmp_path, monkeypatch, utc):  # noqa: ARG001
    with _client(request.param, tmp_path / "engine", monkeypatch) as c:
        c.scenario = request.param
        yield c


def test_the_account_has_the_paper_accounts_shape(client: TestClient) -> None:
    s = client.get("/api/v1/portfolio/summary").json()
    assert s["positions_count"] == 37
    assert s["gross_exposure"] == pytest.approx(1.025, abs=0.003)  # all long: a margin loan pays for the rest
    assert s["short_exposure"] == 0
    assert s["cash"] == pytest.approx(-26_300, abs=500)
    assert s["nav"] == pytest.approx(1_051_943, rel=0.01)
    held = {p["symbol"] for p in client.get("/api/v1/portfolio/positions").json()["positions"]}
    assert {i.symbol for i in MODEL} | {"TQQQ"} | OUTSIDE <= held
    st = client.get("/api/v1/status").json()
    assert st["mode"] == "paper" and st["account"]["id"].startswith("DU")
    comp = {c["id"]: c for c in st["components"]}
    assert comp["gateway"]["status"] == "ok" and comp["market_data"]["status"] == "warning"  # delayed quotes
    assert client.get("/api/v1/market/strip").json()["delayed"] is True
    assert client.get("/api/v1/trading/orders").json()["orders"] == []


def test_fresh_is_a_new_setup(tmp_path, monkeypatch, utc) -> None:  # noqa: ARG001
    with _client("fresh", tmp_path / "engine", monkeypatch) as c:
        s = c.get("/api/v1/portfolio/summary").json()
        assert s["history"] == {"first_date": "2026-10-12", "days": 1, "complete": False, "sources": ["live NAV"]}
        res = c.get("/api/v1/performance")
        assert res.status_code == 503 and res.json()["title"] == "Not enough NAV history yet"
        job = c.get("/api/v1/status").json()["schedule"][0]
        assert job["label"] == "Daily run: data, targets, rebalance if drift > 5%; orders go market-on-close"
        assert job["status"] == "scheduled" and job["detail"].startswith("15:35 ET")  # 14:30 ET: still to come
        assert [r["status"] for r in c.get("/api/v1/ops/doctor").json()["results"]] == ["SKIP"]
        assert c.get("/api/v1/ops/runs").json()["runs"] == []
        preview = c.get("/api/v1/trading/rebalance/preview").json()
        assert preview["targets_date"] == "2026-05-06"
        assert next(x for x in preview["checks"] if x["id"] == "data")["status"] == "fail"  # stale targets
        assert len(c.get("/api/v1/portfolio/intraday").json()["points"]) == 1  # sampling just started: now only


def test_history_has_been_running(tmp_path, monkeypatch, utc) -> None:  # noqa: ARG001
    with _client("history", tmp_path / "engine", monkeypatch) as c:
        h = c.get("/api/v1/portfolio/summary").json()["history"]
        assert h["days"] == staging.FLEX_DAYS + 1 and h["complete"] is True  # Flex through Friday, plus today
        assert h["sources"] == ["IBKR Flex", "live NAV"]
        p = c.get("/api/v1/performance", params={"range": "ITD"}).json()
        assert p["start"] == h["first_date"] and p["end"] == "2026-10-12"
        assert p["stats"]["sharpe"] is not None
        job = c.get("/api/v1/status").json()["schedule"][0]
        assert job["status"] == "scheduled" and job["detail"].startswith("15:35 ET")  # 14:30 ET: still to come
        runs = c.get("/api/v1/ops/runs").json()["runs"]
        assert runs[0]["kind"] == "doctor" or runs[0]["ok"]
        assert any(not r["ok"] for r in runs) and any(r["summary"].startswith("Rebalanced") for r in runs)
        preview = c.get("/api/v1/trading/rebalance/preview").json()
        assert preview["targets_date"] == "2026-10-09"
        assert next(x for x in preview["checks"] if x["id"] == "data")["status"] == "pass"
        # the API ran all day: a sample every 15s since the open (5 hours ago), then now
        assert len(c.get("/api/v1/portfolio/intraday").json()["points"]) in range(1201, 1204)


@pytest.mark.parametrize(("path", "url"), GETS)
def test_every_get_matches_the_contract(client: TestClient, path: str, url: str) -> None:
    res = client.get(f"/api/v1{url}")
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema(path, "get"))


@pytest.mark.parametrize("rng", ["1M", "3M", "6M", "YTD", "1Y", "ITD"])
@pytest.mark.parametrize("bench", ["SPY", "6040", "ACWI"])
def test_performance_matches_the_contract(client: TestClient, rng: str, bench: str) -> None:
    res = client.get("/api/v1/performance", params={"range": rng, "benchmark": bench})
    if client.scenario == "fresh":
        assert res.status_code == 503
        assert_schema(res.json(), PROBLEM)
    else:
        assert res.status_code == 200, res.text
        assert_schema(res.json(), response_schema("/performance", "get"))
    assert_schema(client.get("/api/v1/benchmarks").json(), response_schema("/benchmarks", "get"))


def test_every_post_answers_in_contract_terms(client: TestClient) -> None:
    doc = client.post("/api/v1/ops/doctor/run")
    assert doc.status_code == 200, doc.text
    assert_schema(doc.json(), response_schema("/ops/doctor/run", "post"))
    socket = {"name": "socket", "status": "PASS", "detail": "4002 listening (IB Gateway paper)", "fix": ""}
    assert doc.json()["results"][1] == socket
    assert client.get("/api/v1/ops/doctor").json() == doc.json()
    for body in ({"armed": True, "reason": "staging"}, {"armed": False}):
        res = client.post("/api/v1/ops/kill-switch", json=body)
        assert res.status_code == 200, res.text
        assert_schema(res.json(), response_schema("/ops/kill-switch", "post"))
        assert res.json()["armed"] is body["armed"]
    # Submit is refused (fresh: stale targets; history: staging never starts the daily runner)
    res = client.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
    assert res.status_code == 409
    assert_schema(res.json(), PROBLEM)
    if client.scenario == "history":
        assert res.json()["title"] == "Staging never starts the daily runner"
    res = client.post("/api/v1/trading/orders/1/cancel")
    assert res.status_code == 409
    assert_schema(res.json(), PROBLEM)


def test_quotes_and_pnl_move_during_the_session(tmp_path, monkeypatch, utc) -> None:  # noqa: ARG001
    app = staging.create_staging_app("fresh", tmp_path / "engine", NOW, monkeypatch)
    clock = app.state.staging_clock
    with TestClient(app) as c:
        before = c.get("/api/v1/portfolio/summary").json()
        strip = c.get("/api/v1/market/strip").json()["items"]
        points = len(c.get("/api/v1/portfolio/intraday").json()["points"])
        clock.advance(60)
        after = c.get("/api/v1/portfolio/summary").json()
        assert after["nav"] != before["nav"] and after["day_pnl"] != before["day_pnl"]
        assert c.get("/api/v1/market/strip").json()["items"] != strip
        # a sample every 15s piles up
        assert len(c.get("/api/v1/portfolio/intraday").json()["points"]) >= points + 3


def test_staging_never_touches_the_real_engine_tree(tmp_path, monkeypatch, utc) -> None:  # noqa: ARG001
    from src.execution import kill_switch
    from src.production.runtime import build_paper_basket

    root = tmp_path / "engine"
    with _client("history", root, monkeypatch):
        assert engine.ENGINE_ROOT == root
        assert str(kill_switch.KILL_SWITCH_PATH).startswith(str(root))
        assert build_paper_basket.default_config().targets_path.is_relative_to(root)
        assert not any(k.startswith("IBKR_FLEX") for k in os.environ)


def test_the_same_moment_gives_the_same_account(tmp_path, monkeypatch, utc) -> None:  # noqa: ARG001
    staging.isolate(tmp_path / "engine", lambda: NOW, monkeypatch)  # never reads .env for the endpoint
    a = staging.StagingBroker(lambda: NOW)
    b = staging.StagingBroker(lambda: NOW)
    monkeypatch.setattr(staging, "day", staging.Day)  # no cache: computed twice
    assert a.snapshot() == b.snapshot() and a.quotes() == b.quotes()
    assert staging.Moment.at(NOW).last_close == date(2026, 10, 9)
