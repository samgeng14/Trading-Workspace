"""The no-leverage mandate through the API: the C++ check over every holding on
the rebalance preview, the submit and the risk report. Holdings outside the
model are sold at the next rebalance, and TQQQ, out of the model since
2026-10-12, counts at 3x until it is."""

from __future__ import annotations

import math
import re
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
# held, but not in the model: about the 15 the paper account held on 2026-10-12, at IBKR's marks
OUTSIDE = {
    "XLE": 90.0, "XLU": 80.0, "DBC": 20.0, "XLK": 230.0, "XLV": 150.0, "XLP": 80.0, "XLY": 200.0, "XLB": 90.0,
    "XLRE": 42.0, "XBI": 95.0, "SMH": 260.0, "ARKK": 60.0, "EEM": 47.0, "HYG": 80.0, "LQD": 110.0,
}  # fmt: skip
TQQQ_PX = 95.0


def etf(con_id: int, symbol: str, qty: float, px: float) -> PositionSnap:
    return PositionSnap(con_id, symbol, symbol, "ETF", qty, 1, px, px, px, qty * px, 0.0, 0.0)


def model_book(invested: float, outside: float) -> list[PositionSnap]:
    """The model's ETFs at ``invested`` of NAV, priced at the targets' own closes so the
    basket's prices match, plus ``outside`` of NAV in holdings the model doesn't have.

    The model is the targets file limited to the model universe, weights scaled to sum
    to 1: the committed targets were written before TQQQ left the model and still list it.
    """
    from src.production.generate_targets import model_universe
    from src.production.runtime.build_paper_basket import default_config, load_latest_targets

    targets = load_latest_targets(default_config().targets_path)
    targets = targets.loc[targets["symbol"].isin(model_universe())]
    total = float(targets["target_weight"].sum())
    out = [
        etf(
            i,
            str(t.symbol),
            math.floor(invested * NAV * float(t.target_weight) / total / float(t.close)),
            float(t.close),
        )
        for i, t in enumerate(targets.itertuples(index=False), start=1)
    ]
    for j, (sym, px) in enumerate(OUTSIDE.items(), start=100):
        out.append(etf(j, sym, math.floor(outside * NAV / len(OUTSIDE) / px), px))
    return out


def tqqq(weight: float) -> PositionSnap:
    """TQQQ worth ``weight`` of NAV by market value: three times that in exposure."""
    return etf(200, "TQQQ", math.floor(weight * NAV / TQQQ_PX), TQQQ_PX)


def broker_with(positions: list[PositionSnap]) -> FakeBroker:
    """A connected fake broker holding ``positions``, with cash making NAV exactly $1M."""
    cash = NAV - sum(p.market_value for p in positions)
    b = FakeBroker()
    b.snapshot = lambda: AccountSnap(  # type: ignore[method-assign]
        "DU1234567", "USD", NAV, cash, 0.0, datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc), positions
    )
    return b


def levered() -> FakeBroker:
    # like the paper account on 2026-10-12: the model at 90%, 15 holdings outside it at 10% and
    # TQQQ at 2.5%, so 102.5% long by market value on a margin loan of about $25k
    return broker_with([*model_book(0.90, 0.10), tqqq(0.025)])


def unlevered() -> FakeBroker:
    return broker_with(model_book(0.90, 0.0))


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


def test_the_rebalance_sells_holdings_outside_the_model_and_the_book_comes_back_inside(
    engine_tree,  # noqa: ARG001
    targets_day,  # noqa: ARG001
    started,
) -> None:
    broker = levered()
    held = {p.symbol: p for p in broker.snapshot().positions}
    with client_for(broker) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
        checks = checks_of(body)
        assert checks["data"]["status"] == "pass"

        # TQQQ and the 15 others: every one is sold whole, whatever its size
        outside = sorted([*OUTSIDE, "TQQQ"])
        un = checks["untargeted"]
        assert un["status"] == "warn"
        assert un["detail"] == f"{', '.join(outside)}: not in the model, so this rebalance sells them"
        rows = {r["symbol"]: r for r in body["rows"]}
        for sym in outside:
            r = rows[sym]
            assert (r["target_weight"], r["target_qty"]) == (0.0, 0)
            assert r["trade_qty"] == -r["current_qty"] == -held[sym].quantity
            assert r["est_notional"] == pytest.approx(-held[sym].market_value)

        # before: 102.5% long by market value, 107.5% with TQQQ at 3x, on a margin loan.
        # after: the model alone, under 100% gross, with cash left over
        lev = checks["leverage"]
        assert lev["status"] == "pass", lev
        before, after, cash = re.match(r"gross (\S+)% → (\S+)% of NAV, cash (\S+) after", lev["detail"]).groups()
        assert float(before) == pytest.approx(107.5, abs=0.5) and float(after) < 100.0
        assert not cash.startswith("-")
        assert [c["id"] for c in body["checks"] if c["status"] == "fail"] == []
        ids = [c["id"] for c in body["checks"]]
        assert ids.index("leverage") == ids.index("gate") + 1  # right after the engine's own gate
        assert body["can_submit"] is True
        assert c.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"}).status_code == 200
    assert started == ["paper"]


def test_an_outside_holding_with_no_price_blocks_the_rebalance(engine_tree, targets_day, started) -> None:  # noqa: ARG001
    # no IBKR price, no market value, not in the price master: the sell can't be measured, so nothing goes
    zzz = PositionSnap(999, "ZZZ", "ZZZ", "STK", 1_000, 1, 0.0, None, None, 0.0, 0.0, None)
    with client_for(broker_with([*model_book(0.90, 0.0), zzz])) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
        checks = checks_of(body)
        row = {r["symbol"]: r for r in body["rows"]}["ZZZ"]
        assert row["trade_qty"] == -1_000 and row["est_notional"] == 0.0
        lev = checks["leverage"]
        assert lev["status"] == "fail" and "no usable price for ZZZ" in lev["detail"]
        assert [c["id"] for c in body["checks"] if c["status"] == "fail"] == ["leverage"]
        assert body["can_submit"] is False

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
        assert re.match(r"gross (89|90)\.\d% → 9\d\.\d% of NAV", lev["detail"]), lev["detail"]
        assert "every holding counted" in lev["detail"]
        assert body["can_submit"] is True
        res = c.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
        assert res.status_code == 200, res.text
    assert started == ["paper"]


def test_holdings_outside_the_model_no_longer_tip_it(engine_tree, targets_day) -> None:  # noqa: ARG001
    # 1.5% outside the model used to stay while the basket refilled the model to 99.5%: over 100%.
    # The basket now sells it, so the refilled book is inside
    with client_for(broker_with(model_book(0.90, 0.015))) as c:
        lev = checks_of(c.get("/api/v1/trading/rebalance/preview").json())["leverage"]
    assert lev["status"] == "pass", lev


def test_tqqq_counts_at_three_times_its_value(engine_tree, frozen_now) -> None:  # noqa: ARG001
    # 99.5% of NAV by market value, cash 0.5%: inside by value, but TQQQ's 3% is 9% of exposure
    broker = broker_with([*model_book(0.965, 0.0), tqqq(0.03)])
    with client_for(broker) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
    assert limits["gross"]["value"] == pytest.approx(0.965 + 3 * 0.03, abs=0.003)
    assert limits["gross"]["status"] == "breach" and limits["cash"]["status"] == "ok"

    # and adding to it names it: the C++ check counts the order at 3x too
    res = mandate.check(broker.snapshot(), [{"symbol": "TQQQ", "quantity": 10, "price": TQQQ_PX}])
    assert not res["ok"]
    gross = next(b for b in res["breaches"] if b["rule"] == "gross")
    assert gross["blocking"] and "(counting TQQQ 3x at that exposure)" in gross["message"]
    assert mandate.exposure_of("TQQQ") == 3.0 and mandate.exposure_of("TQQQ", "FUT") == 1.0


def test_risk_shows_gross_and_cash_breaches(engine_tree, frozen_now) -> None:  # noqa: ARG001
    with client_for(levered()) as c:
        limits = limits_of(c.get("/api/v1/risk").json())
    gross, cash = limits["gross"], limits["cash"]
    assert gross["status"] == "breach" and gross["limit"] == 1.0
    # 102.5% by market value; TQQQ's 2.5% counts three times
    assert gross["value"] == pytest.approx(1.075, abs=0.005)
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
    # but its basket refills the ETFs, and the MES short, which the basket leaves alone, then
    # puts gross over 100%
    checks = checks_of(preview)
    lev = checks["leverage"]
    assert lev["status"] == "fail" and "over the 100.0% limit" in lev["detail"]
    kept = checks["untargeted_kept"]
    assert kept["status"] == "warn" and kept["detail"].startswith("MESZ6: outside the model, but not sold")
    assert "untargeted" not in checks and "MESZ6" not in {r["symbol"] for r in preview["rows"]}


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
