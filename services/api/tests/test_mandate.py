"""The no-leverage mandate through the API: the C++ check over every holding on
the rebalance preview, the submit and the risk report."""

from __future__ import annotations

import math
import sys
from datetime import datetime, time, timezone

import pytest
from fastapi.testclient import TestClient

from dv02_api import mandate, runs
from dv02_api.app import create_app
from dv02_api.broker import AccountSnap, PositionSnap

from .conftest import FakeBroker
from .test_contract import SPEC, assert_schema, response_schema

NAV = 1_000_000.0
OUTSIDE = {"XLE": 90.0, "XLU": 80.0, "DBC": 20.0}  # held, but not in the model


def etf(con_id: int, symbol: str, qty: float, px: float) -> PositionSnap:
    return PositionSnap(con_id, symbol, symbol, "ETF", qty, 1, px, px, px, qty * px, 0.0, 0.0)


def model_book(invested: float, outside: float) -> list[PositionSnap]:
    """The model's ETFs at ``invested`` of NAV, priced at the targets' own closes so the
    basket's prices match, plus ``outside`` of NAV in holdings the model doesn't have."""
    from src.production.runtime.build_paper_basket import default_config, load_latest_targets

    targets = load_latest_targets(default_config().targets_path)
    out = [
        etf(i, str(t.symbol), math.floor(invested * NAV * float(t.target_weight) / float(t.close)), float(t.close))
        for i, t in enumerate(targets.itertuples(index=False), start=1)
    ]
    for j, (sym, px) in enumerate(OUTSIDE.items(), start=100):
        out.append(etf(j, sym, math.floor(outside * NAV / len(OUTSIDE) / px), px))
    return out


def broker_with(positions: list[PositionSnap]) -> FakeBroker:
    """A connected fake broker holding ``positions``, with cash making NAV exactly $1M."""
    cash = NAV - sum(p.market_value for p in positions)
    b = FakeBroker()
    b.snapshot = lambda: AccountSnap(  # type: ignore[method-assign]
        "DU1234567", "USD", NAV, cash, 0.0, datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc), positions
    )
    return b


def levered() -> FakeBroker:
    # like the paper account today: 102.5% long, a margin loan, holdings outside the model
    return broker_with(model_book(0.90, 0.125))


def unlevered() -> FakeBroker:
    return broker_with(model_book(0.90, 0.003))


@pytest.fixture()
def targets_day(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """The evening of the targets' own close, so the data check passes and only the mandate decides."""
    from src.production.runtime.build_paper_basket import default_config, load_latest_targets

    from dv02_api import app as app_mod

    day = load_latest_targets(default_config().targets_path)["date"].max().date()
    now = datetime.combine(day, time(22, 30), tzinfo=timezone.utc)
    monkeypatch.setattr(app_mod, "_now", lambda: now)
    return now


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(runs, "start_daily_run", lambda profile: calls.append(profile) or True)
    return calls


def client_for(broker: FakeBroker) -> TestClient:
    return TestClient(create_app("paper", broker=broker, background=False))


def checks_of(body: dict) -> dict[str, dict]:
    assert_schema(body, response_schema("/trading/rebalance/preview", "get"))
    return {c["id"]: c for c in body["checks"]}


def limits_of(body: dict) -> dict[str, dict]:
    assert_schema(body, response_schema("/risk", "get"))
    return {lim["id"]: lim for lim in body["limits"]}


def test_a_levered_book_is_blocked_and_cannot_submit(engine_tree, targets_day, started) -> None:  # noqa: ARG001
    with client_for(levered()) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
        checks = checks_of(body)
        assert checks["data"]["status"] == "pass"
        lev = checks["leverage"]
        assert lev["label"] == "No leverage" and lev["status"] == "fail"
        assert "gross" in lev["detail"] and "margin loan" in lev["detail"]
        assert [c["id"] for c in body["checks"] if c["status"] == "fail"] == ["leverage"]
        # it sits right after the engine's own gate
        ids = [c["id"] for c in body["checks"]]
        assert ids.index("leverage") == ids.index("gate") + 1
        assert body["can_submit"] is False

        un = checks["untargeted"]
        assert un["status"] == "warn" and "XLE" in un["detail"] and "no-leverage check" in un["detail"]

        res = c.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
        assert res.status_code == 409
        assert_schema(res.json(), SPEC["components"]["schemas"]["Problem"])
        assert "No leverage:" in res.json()["detail"]
    assert started == []


def test_an_unlevered_book_passes_counting_every_holding(engine_tree, targets_day, started) -> None:  # noqa: ARG001
    with client_for(unlevered()) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
        lev = checks_of(body)["leverage"]
        assert lev["status"] == "pass", lev
        assert lev["detail"].startswith("gross 90.") and "every holding counted" in lev["detail"]
        assert body["can_submit"] is True
        res = c.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
        assert res.status_code == 200, res.text
    assert started == ["paper"]


def test_holdings_outside_the_model_are_what_tips_it(engine_tree, targets_day) -> None:  # noqa: ARG001
    # the same model book with 1.5% outside it: the basket refills the model to 99.5%, so it is over
    with client_for(broker_with(model_book(0.90, 0.015))) as c:
        lev = checks_of(c.get("/api/v1/trading/rebalance/preview").json())["leverage"]
    assert lev["status"] == "fail" and "over the 100.0% limit" in lev["detail"]


def test_risk_shows_gross_and_cash_breaches(engine_tree, frozen_now) -> None:  # noqa: ARG001
    with client_for(levered()) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
    gross, cash = limits["gross"], limits["cash"]
    assert gross["status"] == "breach" and gross["limit"] == 1.0
    assert gross["value"] == pytest.approx(1.025, abs=0.005)
    assert cash["label"] == "Cash (no margin loan)" and cash["unit"] == "pct" and cash["limit"] == 0.0
    assert cash["status"] == "breach" and cash["value"] == pytest.approx(-0.025, abs=0.005)
    for lim in (gross, cash):
        assert "the engine blocks orders that would leave" in lim["detail"] and "every holding counts" in lim["detail"]
    assert limits["single_name"]["limit"] == 0.25


def test_a_fully_invested_book_is_ok_not_a_warning(engine_tree, frozen_now) -> None:  # noqa: ARG001
    # 99.5% gross is where the model sits by design: no early warning on the mandate
    with client_for(broker_with(model_book(0.995, 0.0))) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
    assert limits["gross"]["value"] == pytest.approx(0.995, abs=0.002)
    assert limits["gross"]["status"] == "ok" and limits["cash"]["status"] == "ok"


def test_a_book_the_engine_cannot_measure_breaches_gross(engine_tree, frozen_now) -> None:  # noqa: ARG001
    # 50% invested plus a holding with no price and no market value: gross would read 50%, but
    # the engine blocks every order until it can price ZZZ, so the risk report says so
    zzz = PositionSnap(999, "ZZZ", "ZZZ", "STK", 1_000, 1, 0.0, None, None, 0.0, 0.0, None)
    with client_for(broker_with([*model_book(0.5, 0.0), zzz])) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
    gross = limits["gross"]
    assert gross["status"] == "breach" and "no usable price for ZZZ" in gross["detail"]
    assert gross["value"] == pytest.approx(0.5, abs=0.005)
    assert limits["cash"]["status"] == "ok"


def test_the_largest_position_is_the_one_the_engine_measures(engine_tree, frozen_now) -> None:  # noqa: ARG001
    # a short is a position too: 30% of NAV short XLE is over the 25% single-name limit
    xle = etf(999, "XLE", -3_000, 100.0)
    with client_for(broker_with([*model_book(0.6, 0.0), xle])) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
    single = limits["single_name"]
    assert single["value"] == pytest.approx(0.30) and single["status"] == "breach"
    assert single["detail"].startswith("XLE")


def test_the_default_fake_book_is_inside_the_mandate_but_its_basket_is_not(engine_tree, frozen_now) -> None:  # noqa: ARG001
    with client_for(FakeBroker()) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
        preview = c.get("/api/v1/trading/rebalance/preview").json()
    # SPY, TLT, GLD long and the MES short at its full notional
    assert limits["gross"]["status"] == "ok"
    assert limits["gross"]["value"] == pytest.approx((244_800 + 173_000 + 126_000 + 166_250) / 1_025_000)
    assert limits["cash"]["status"] == "ok" and limits["cash"]["value"] == pytest.approx(480_000 / 1_025_000)
    # but its basket refills the ETFs to 99.5% of NAV, and the MES short then puts gross over 100%
    lev = checks_of(preview)["leverage"]
    assert lev["status"] == "fail" and "over the 100.0% limit" in lev["detail"]


def test_missing_engine_fails_closed(engine_tree, targets_day, started, monkeypatch) -> None:  # noqa: ARG001
    monkeypatch.setitem(sys.modules, "dv02_engine", None)
    monkeypatch.setitem(sys.modules, "dv02_engine.risk", None)
    with pytest.raises(mandate.EngineMissing):
        mandate.engine()
    with client_for(unlevered()) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
        lev = checks_of(body)["leverage"]
        assert lev["status"] == "fail" and sys.executable in lev["detail"] and "bin/tradeit setup" in lev["detail"]
        assert body["can_submit"] is False
        assert c.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"}).status_code == 409
        limits = limits_of(c.get("/api/v1/risk").json())
    for lim in (limits["gross"], limits["cash"]):
        assert lim["status"] == "breach" and "bin/tradeit setup" in lim["detail"]
    assert started == []


def test_unpriced_positions_price_from_market_value_else_block() -> None:
    p = etf(1, "XLE", 100, 90.0)
    p.last = None
    assert mandate.price_of(p) == pytest.approx(90.0)
    p.market_value = float("nan")
    assert mandate.price_of(p) == 0.0
    fut = PositionSnap(2, "MESZ6", "MES", "FUT", -5, 5, 6600.0, None, None, -166_250.0, 0.0, 0.0)
    assert mandate.price_of(fut) == pytest.approx(6650.0)
    snap = AccountSnap("DU1", "USD", NAV, NAV, 0.0, datetime(2026, 10, 9, tzinfo=timezone.utc), [p])
    res = mandate.check(snap, [])
    assert not res["ok"] and res["breaches"][0]["rule"] == "unpriced"
