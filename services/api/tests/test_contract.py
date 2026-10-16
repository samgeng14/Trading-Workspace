"""Every endpoint against schemas/api/v1.yaml, with a fake broker and a temp engine tree.

Object schemas are tightened to additionalProperties: false here, so a typo'd
or leaked field fails the test instead of being silently ignored by the UI.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from dv02_api import runs
from dv02_api.app import create_app

SPEC = yaml.safe_load((Path(__file__).resolve().parents[3] / "schemas" / "api" / "v1.yaml").read_text())


def _strict(node: Any) -> Any:
    if isinstance(node, dict):
        node = {k: _strict(v) for k, v in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node.setdefault("additionalProperties", False)
        return node
    if isinstance(node, list):
        return [_strict(v) for v in node]
    return node


_DOC = _strict(copy.deepcopy(SPEC))
_REGISTRY = Registry().with_resource("urn:v1", Resource.from_contents(_DOC, default_specification=DRAFT202012))


def assert_schema(body: Any, schema: dict[str, Any]) -> None:
    def rewrite(n: Any) -> Any:
        if isinstance(n, dict):
            return {k: ("urn:v1" + v if k == "$ref" and v.startswith("#") else rewrite(v)) for k, v in n.items()}
        if isinstance(n, list):
            return [rewrite(v) for v in n]
        return n

    validator = Draft202012Validator(rewrite(schema), registry=_REGISTRY)
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])


def response_schema(path: str, method: str, status: int = 200) -> dict[str, Any]:
    op = SPEC["paths"][path][method]
    return op["responses"][str(status)]["content"]["application/json"]["schema"]


@pytest.fixture()
def client(engine_tree, fake_broker, frozen_now):  # noqa: ARG001
    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as c:
        yield c


GETS = [
    ("/status", "/status"),
    ("/market/strip", "/market/strip"),
    ("/portfolio/summary", "/portfolio/summary"),
    ("/portfolio/positions", "/portfolio/positions"),
    ("/portfolio/intraday", "/portfolio/intraday"),
    ("/benchmarks", "/benchmarks"),
    ("/risk", "/risk"),
    ("/trading/orders", "/trading/orders"),
    ("/trading/rebalance/preview", "/trading/rebalance/preview"),
    ("/ops/doctor", "/ops/doctor"),
    ("/ops/runs", "/ops/runs"),
]


@pytest.mark.parametrize(("path", "url"), GETS)
def test_get_matches_contract(client: TestClient, path: str, url: str) -> None:
    res = client.get(f"/api/v1{url}")
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema(path, "get"))


@pytest.mark.parametrize("rng", ["1W", "MTD", "1M", "3M", "6M", "YTD", "1Y", "ITD"])
@pytest.mark.parametrize("bench", ["SPY", "6040"])
def test_performance_every_range(client: TestClient, rng: str, bench: str) -> None:
    res = client.get("/api/v1/performance", params={"range": rng, "benchmark": bench})
    assert res.status_code == 200, res.text
    body = res.json()
    assert_schema(body, response_schema("/performance", "get"))
    assert body["series"][0]["portfolio"] == pytest.approx(100)
    assert body["series"][0]["benchmark"] == pytest.approx(100)


def test_unknown_benchmark_is_a_problem(client: TestClient) -> None:
    res = client.get("/api/v1/performance", params={"benchmark": "NOPE"})
    assert res.status_code == 400
    assert_schema(res.json(), SPEC["components"]["schemas"]["Problem"])


def test_summary_numbers(client: TestClient) -> None:
    s = client.get("/api/v1/portfolio/summary").json()
    assert s["nav"] == 1_025_000.0
    assert s["inception_date"] == "2025-10-15"
    assert s["itd_return"] == pytest.approx(0.025)  # vs $1M inception capital
    assert s["day_pnl"] == 2_950.0
    assert s["short_exposure"] < 0  # the MES hedge
    assert s["positions_count"] == 4
    assert len(s["nav_history"]) == 30


def test_positions_carry_targets_and_futures_notional(client: TestClient) -> None:
    pos = {p["symbol"]: p for p in client.get("/api/v1/portfolio/positions").json()["positions"]}
    assert pos["MESZ6"]["asset_class"] == "index_future"
    assert pos["MESZ6"]["sec_type"] == "FUT"
    assert pos["MESZ6"]["target_weight"] is None
    assert pos["SPY"]["name"] == "SPDR S&P 500"
    assert pos["SPY"]["day_return"] == pytest.approx(612 / 608 - 1)


def test_off_hours_rows_take_the_day_move_from_ibkrs_day_pnl(client: TestClient, fake_broker) -> None:
    """With no previous close quoted (IBKR's -1 off hours), the day's move comes from
    IBKR's day P&L for the position instead of reading as 0%."""
    import dataclasses

    snap = fake_broker.snapshot()
    spy = next(p for p in snap.positions if p.symbol == "SPY")
    off_hours = dataclasses.replace(spy, prev_close=None)  # last 612, day P&L +1,600 on 400 shares
    snap = dataclasses.replace(snap, positions=[off_hours if p is spy else p for p in snap.positions])
    fake_broker.snapshot = lambda: snap
    body = client.get("/api/v1/portfolio/positions").json()
    assert_schema(body, response_schema("/portfolio/positions", "get"))
    row = next(p for p in body["positions"] if p["symbol"] == "SPY")
    assert row["prev_close"] == pytest.approx(608.0)
    assert row["day_return"] == pytest.approx(612 / 608 - 1)
    assert row["day_pnl"] == pytest.approx(1_600.0)


def test_status_reports_delayed_data_and_runs(client: TestClient) -> None:
    st = client.get("/api/v1/status").json()
    comp = {c["id"]: c for c in st["components"]}
    assert comp["gateway"]["status"] == "ok"
    assert comp["market_data"]["status"] == "warning"
    assert st["account"]["id"] == "DU1234567"
    assert st["schedule"][0]["id"] == "daily_run"


def test_runs_are_mapped_from_the_engine_files(client: TestClient) -> None:
    body = client.get("/api/v1/ops/runs").json()
    by_id = {r["id"]: r for r in body["runs"]}
    ok = by_id["daily-20261008_163201"]
    assert ok["ok"] and ok["summary"].startswith("No rebalance")
    assert [s["name"] for s in ok["steps"]][:2] == ["ETF bars", "Targets"]
    failed = by_id["daily-20261007_163203"]
    assert not failed["ok"] and "append_daily failed" in failed["summary"]


def test_kill_switch_round_trip_blocks_the_rebalance(client: TestClient) -> None:
    res = client.post("/api/v1/ops/kill-switch", json={"armed": True})
    assert res.status_code == 400
    res = client.post("/api/v1/ops/kill-switch", json={"armed": True, "reason": "test halt"})
    assert res.status_code == 200
    assert_schema(res.json(), response_schema("/ops/kill-switch", "post"))
    assert res.json()["armed"] is True
    assert client.get("/api/v1/status").json()["kill_switch"]["reason"] == "test halt"
    preview = client.get("/api/v1/trading/rebalance/preview").json()
    ks = next(c for c in preview["checks"] if c["id"] == "kill_switch")
    assert ks["status"] == "fail" and preview["can_submit"] is False
    res = client.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
    assert res.status_code == 409
    assert client.post("/api/v1/ops/kill-switch", json={"armed": False}).json()["armed"] is False


def test_arming_from_the_dashboard_cancels_working_orders(client: TestClient, fake_broker) -> None:
    # the fake broker holds a filled SPY order and a working TLT one: arming cancels the working one only
    res = client.post("/api/v1/ops/kill-switch", json={"armed": True, "reason": "stop"})
    assert res.status_code == 200 and res.json()["armed"] is True
    assert fake_broker.cancelled == ["1002"]
    client.post("/api/v1/ops/kill-switch", json={"armed": False})
    assert fake_broker.cancelled == ["1002"]  # disarming cancels nothing more


def test_kill_switch_test_never_touches_the_real_file(engine_tree) -> None:
    from src.execution import kill_switch

    assert str(kill_switch.KILL_SWITCH_PATH).startswith(str(engine_tree))


def test_submit_needs_the_typed_confirmation(client: TestClient) -> None:
    res = client.post("/api/v1/trading/rebalance", json={"confirm": "yes"})
    assert res.status_code == 409


def test_stale_targets_block_the_submit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The repo's committed targets are from May; months old, so the preview must refuse.
    started: list[str] = []
    monkeypatch.setattr(runs, "start_daily_run", lambda profile: started.append(profile) or True)
    preview = client.get("/api/v1/trading/rebalance/preview").json()
    data = next(c for c in preview["checks"] if c["id"] == "data")
    assert data["status"] == "fail" and preview["can_submit"] is False
    res = client.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
    assert res.status_code == 409 and "Targets and prices" in res.json()["detail"]
    assert started == []


def test_submit_starts_the_engines_daily_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from dv02_api import rebalance

    started: list[str] = []
    monkeypatch.setattr(runs, "start_daily_run", lambda profile: started.append(profile) or True)
    ready = {"checks": [{"id": "data", "label": "x", "status": "pass", "detail": ""}], "can_submit": True,
             "totals": {"orders": 7}}  # fmt: skip
    monkeypatch.setattr(rebalance, "preview", lambda *a, **k: ready)
    res = client.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema("/trading/rebalance", "post"))
    assert started == ["paper"] and "about 7 orders" in res.json()["message"]
    monkeypatch.setattr(runs, "start_daily_run", lambda profile: False)
    assert client.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"}).status_code == 409


def test_cancel_open_order(client: TestClient, fake_broker) -> None:
    res = client.post("/api/v1/trading/orders/1002/cancel")
    assert res.status_code == 200, res.text
    assert_schema(res.json(), response_schema("/trading/orders/{orderId}/cancel", "post"))
    assert res.json()["status"] == "cancelled"
    assert fake_broker.cancelled == ["1002"]
    assert client.post("/api/v1/trading/orders/1001/cancel").status_code == 409  # filled
    assert client.post("/api/v1/trading/orders/999/cancel").status_code == 409  # unknown


def test_offline_falls_back_to_the_engine_snapshot(engine_tree, frozen_now) -> None:  # noqa: ARG001
    from .conftest import FakeBroker

    app = create_app("paper", broker=FakeBroker(connected=False), background=False)
    with TestClient(app) as c:
        s = c.get("/api/v1/portfolio/summary")
        assert s.status_code == 200, s.text
        assert_schema(s.json(), response_schema("/portfolio/summary", "get"))
        assert s.json()["nav"] == 1_021_000.0
        st = c.get("/api/v1/status").json()
        gw = next(x for x in st["components"] if x["id"] == "gateway")
        assert gw["status"] == "error" and "snapshot" in gw["detail"]
        p = c.get("/api/v1/trading/rebalance/preview").json()
        assert p["can_submit"] is False
        assert any(ch["id"] == "broker" and ch["status"] == "fail" for ch in p["checks"])
        assert c.get("/api/v1/market/strip").json()["items"] == []


def _client_with_history(engine_tree, monkeypatch, navs: str | None):
    """No configured start: history is only what the files and the live NAV say."""
    from .conftest import FakeBroker

    monkeypatch.delenv("DV02_INCEPTION_DATE")
    path = engine_tree / "artifacts" / "api" / "nav_paper.csv"
    if navs is None:
        path.unlink()
    else:
        path.write_text(navs)
    return TestClient(create_app("paper", broker=FakeBroker(), background=False))


def test_a_brand_new_record_shows_no_invented_returns(engine_tree, frozen_now, monkeypatch) -> None:  # noqa: ARG001
    (engine_tree / "data" / "broker" / "account" / "paper_account_summary_20261008_163210.csv").unlink()
    with _client_with_history(engine_tree, monkeypatch, None) as c:
        s = c.get("/api/v1/portfolio/summary").json()
        assert_schema(s, response_schema("/portfolio/summary", "get"))
        assert s["itd_return"] is None and s["mtd_return"] is None and s["sharpe_itd"] is None
        assert s["history"] == {"first_date": "2026-10-09", "days": 1, "complete": False, "sources": ["live NAV"]}
        assert s["inception_date"] == "2026-10-09"
        res = c.get("/api/v1/performance")
        assert res.status_code == 503 and "bin/tradeit flex" in res.json()["detail"]


def test_a_short_record_reports_returns_but_no_ratios(engine_tree, frozen_now, monkeypatch) -> None:  # noqa: ARG001
    from datetime import date

    from dv02_api import nyse

    days = nyse.trading_days(date(2026, 9, 25), date(2026, 10, 8))
    rows = "".join(f"{d.isoformat()},{1_000_000 + 1000 * i}\n" for i, d in enumerate(days))
    with _client_with_history(engine_tree, monkeypatch, "date,nav\n" + rows) as c:
        p = c.get("/api/v1/performance", params={"range": "ITD"}).json()
        assert_schema(p, response_schema("/performance", "get"))
        st = p["stats"]
        assert st["observations"] == len(days)  # the recorded days plus today, minus the base day
        assert st["ann_return"] is None and st["sharpe"] is None and st["beta"] is None
        assert st["total_return"] > 0 and p["start"] == "2026-09-25"
        s = c.get("/api/v1/portfolio/summary").json()
        assert s["itd_return"] is not None and s["history"]["complete"] is False
        assert s["mtd_return"] is not None  # the record reaches Sep 30
        assert s["ytd_return"] is None  # but not last Dec 31, and it isn't known to be complete
