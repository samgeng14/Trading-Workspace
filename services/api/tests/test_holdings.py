"""Holdings that aren't ETF shares: IBKR's secType reaches the no-leverage check as it is
(a futures option is an option, FX is FX), the contract gets a type it can express, and
the offline snapshot prices futures by their multiplier and IBKR's own mark."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from dv02_engine import risk
from fastapi.testclient import TestClient

from dv02_api import app as app_mod
from dv02_api import history, mandate
from dv02_api.app import create_app
from dv02_api.broker import Broker

from .conftest import FakeBroker
from .test_contract import assert_schema, response_schema

OPTION = "SPY   261218C00600000"  # IBKR's local symbol for an option


def _item(con_id, symbol, local, sec_type, mult, qty, price, avg_cost):  # noqa: ANN001, ANN202
    contract = SimpleNamespace(conId=con_id, symbol=symbol, localSymbol=local, secType=sec_type, multiplier=mult)
    return SimpleNamespace(contract=contract, position=qty, marketPrice=price, marketValue=qty * price,
                           averageCost=avg_cost, unrealizedPNL=0.0)  # fmt: skip


class _FakeIB:
    """An account holding one of each kind, as ib_async reports them."""

    def __init__(self) -> None:
        self._portfolio = [
            _item(1, "SPY", "SPY", "STK", "", 100, 600.0, 550.0),
            _item(2, "SPY", OPTION, "OPT", "100", 2, 12.5, 1_250.0),  # averageCost is per contract
            _item(3, "ES", "ESZ6 C6800", "FOP", "50", 1, 80.0, 5_000.0),
            _item(4, "EUR", "EUR.USD", "CASH", "", 10_000, 1.08, 1.07),
            _item(5, "US-T", "T 4 1/4 11/15/34", "BOND", "", 10, 99.5, 98.0),
            _item(6, "BTC", "BTC.USD", "CRYPTO", "", 0.5, 60_000.0, 50_000.0),
        ]

    def isConnected(self) -> bool:  # noqa: N802
        return True

    def accountValues(self, account: str):  # noqa: N802
        return [
            SimpleNamespace(tag="NetLiquidation", value="1000000", currency="BASE"),
            SimpleNamespace(tag="TotalCashValue", value="800000", currency="BASE"),
        ]

    def portfolio(self, account: str):
        return self._portfolio


@pytest.fixture()
def live_snap():
    b = Broker("paper")
    b._ib = _FakeIB()
    b.account = "DU1234567"
    b.status.connected = True
    b._pnl = SimpleNamespace(dailyPnL=0.0)
    snap = b.snapshot()
    assert snap is not None
    return snap


def test_each_holding_keeps_its_ibkr_type_and_contract_key(live_snap) -> None:
    by = {p.con_id: p for p in live_snap.positions}
    assert (by[1].symbol, by[1].sec_type) == ("SPY", "ETF")
    # an option keyed by its own symbol, not SPY's, priced per share
    assert (by[2].symbol, by[2].sec_type, by[2].multiplier, by[2].avg_cost) == (OPTION, "OPT", 100.0, 12.5)
    assert (by[3].symbol, by[3].root, by[3].sec_type, by[3].avg_cost) == ("ESZ6 C6800", "ES", "FOP", 100.0)
    assert [by[i].sec_type for i in (4, 5, 6)] == ["CASH", "BOND", "CRYPTO"]  # used to be STK, shares
    assert by[3].market_value == pytest.approx(1 * 80.0 * 50)


def test_the_leverage_check_sees_a_futures_option_as_an_option(live_snap) -> None:
    classes = {p.symbol: risk.asset_class_name(risk.asset_class_from_ibkr(p.sec_type)) for p in live_snap.positions}
    assert classes == {
        "SPY": "etf", OPTION: "option", "ESZ6 C6800": "future_option", "EUR": "fx", "US-T": "bond", "BTC": "crypto",
    }  # fmt: skip
    res = mandate.check(live_snap, [])
    assert not res["ok"]
    options = next(b for b in res["breaches"] if b["rule"] == "options")
    assert options["blocking"] and "ESZ6 C6800" in options["message"] and OPTION in options["message"]


def test_positions_show_each_kind_in_the_contracts_terms(engine_tree, frozen_now, live_snap) -> None:  # noqa: ARG001
    broker = FakeBroker()
    broker.snapshot = lambda: live_snap
    with TestClient(create_app("paper", broker=broker, background=False)) as c:
        body = c.get("/api/v1/portfolio/positions").json()
        assert_schema(body, response_schema("/portfolio/positions", "get"))
        assert_schema(c.get("/api/v1/risk").json(), response_schema("/risk", "get"))
    rows = {r["symbol"]: (r["sec_type"], r["asset_class"]) for r in body["positions"]}
    assert rows == {
        "SPY": ("ETF", "equity"),
        OPTION: ("OPT", "equity"),
        "ESZ6 C6800": ("OPT", "index_future"),  # the contract has no FOP; an option on ES
        "EUR": ("STK", "cash"),  # nor CASH, BOND or CRYPTO: their asset class says what they are
        "US-T": ("STK", "fixed_income"),
        "BTC": ("STK", "crypto"),
    }


# ── offline: the engine's saved snapshot ───────────────────────────────

SNAPSHOT = f"""symbol,current_shares,avg_cost,sec_type,multiplier,market_price
GLD,300,400.0,STK,1.0,421.5
MESZ6,-5,33000.0,FUT,5.0,6650.0
SPY,400,600.0,STK,1.0,612.0
{OPTION},2,1250.0,OPT,100.0,
ZNZ6,3,112000.0,FUT,1000.0,111.5
"""
SAVED_AT = datetime(2026, 10, 8, 20, 32, tzinfo=timezone.utc)  # 16:32 ET, after Thursday's close
NAV = 1_021_000.0  # the account summary conftest saves


@pytest.fixture()
def saved(engine_tree):
    path = engine_tree / "data" / "broker" / "positions" / "paper_positions_snapshot.csv"
    path.write_text(SNAPSHOT)
    os.utime(path, (SAVED_AT.timestamp(), SAVED_AT.timestamp()))
    return path


def test_offline_futures_are_futures_at_their_multiplier_and_mark(saved) -> None:  # noqa: ARG001
    snap = app_mod.offline_snapshot("paper")
    assert snap is not None
    by = {p.symbol: p for p in snap.positions}
    mes = by["MESZ6"]
    assert (mes.sec_type, mes.root, mes.multiplier, mes.avg_cost, mes.last) == ("FUT", "MES", 5.0, 6600.0, 6650.0)
    assert mes.market_value == pytest.approx(-5 * 6650.0 * 5)
    assert (by["ZNZ6"].root, by["ZNZ6"].market_value) == ("ZN", pytest.approx(3 * 111.5 * 1000))
    # shares at IBKR's mark when the snapshot was saved, the same moment as its NAV
    spy = by["SPY"]
    before = next(c for d, c in reversed(history.closes("SPY")) if d < date(2026, 10, 8))
    assert (spy.sec_type, spy.last, spy.prev_close, spy.market_value) == ("ETF", 612.0, before, 400 * 612.0)
    # IBKR had no mark for the option and there are no closes: no price, no value, never its cost
    opt = by[OPTION]
    assert (opt.sec_type, opt.last, opt.market_value, opt.avg_cost) == ("OPT", None, 0.0, 12.5)


def test_the_offline_leverage_check_counts_futures_at_notional(saved) -> None:  # noqa: ARG001
    snap = app_mod.offline_snapshot("paper")
    gross = (300 * 421.5 + 400 * 612.0 + 5 * 6650.0 * 5 + 3 * 111.5 * 1000) / NAV
    book = mandate.exposure(snap)
    assert book["gross"] == pytest.approx(gross)
    assert any(OPTION in m for m in book["unmeasured"])  # unpriced, and an option: blocks


def test_offline_views_and_preview(saved, frozen_now) -> None:  # noqa: ARG001
    with TestClient(create_app("paper", broker=FakeBroker(connected=False), background=False)) as c:
        body = c.get("/api/v1/portfolio/positions").json()
        assert_schema(body, response_schema("/portfolio/positions", "get"))
        rows = {r["symbol"]: r for r in body["positions"]}
        mes = rows["MESZ6"]
        assert (mes["sec_type"], mes["multiplier"], mes["asset_class"], mes["name"]) == (
            "FUT", 5.0, "index_future", "Micro E-mini S&P 500",
        )  # fmt: skip
        assert mes["market_value"] == pytest.approx(-166_250.0)
        assert rows["ZNZ6"]["asset_class"] == "fixed_income"
        assert rows["SPY"]["last"] == 612.0
        assert_schema(c.get("/api/v1/risk").json(), response_schema("/risk", "get"))
        preview = c.get("/api/v1/trading/rebalance/preview").json()
        assert_schema(preview, response_schema("/trading/rebalance/preview", "get"))
        assert preview["can_submit"] is False
        # futures and options are never traded as if they were shares
        assert all(r["trade_qty"] == 0 for r in preview["rows"] if r["symbol"] in ("MESZ6", "ZNZ6", OPTION))


def test_an_offline_future_of_unknown_size_is_unpriced_not_one_x(saved) -> None:
    # the engine saves a multiplier IBKR left empty as empty: at 1x, 10 MES would count $66,500, not $332,500
    saved.write_text(
        "symbol,current_shares,avg_cost,sec_type,multiplier,market_price\nMESZ6,10,33000.0,FUT,,6650.0\n"
        "SPY,400,600.0,STK,1.0,612.0\n"
    )
    snap = app_mod.offline_snapshot("paper")
    mes = next(p for p in snap.positions if p.symbol == "MESZ6")
    assert (mes.last, mes.market_value) == (None, 0.0)
    book = mandate.exposure(snap)
    assert book["gross"] == pytest.approx(400 * 612.0 / NAV)  # only what it can measure
    assert any("MESZ6" in m for m in book["unmeasured"])  # and that blocks every order


def test_a_nan_or_zero_close_is_skipped(saved, frozen_now) -> None:  # noqa: ARG001
    bars = saved.parents[3] / "artifacts" / "api" / "bars" / "GLD.csv"
    lines = bars.read_text().splitlines()
    good = lines[-3].split(",")[1]
    lines[-2] = lines[-2].split(",")[0] + ",0"
    lines[-1] = lines[-1].split(",")[0] + ",nan"
    bars.write_text("\n".join(lines) + "\n")
    saved.write_text("symbol,current_shares,avg_cost,sec_type,multiplier,market_price\nGLD,300,400.0,STK,1.0,\n")
    assert history.closes("GLD")[-1][1] == float(good)
    with TestClient(create_app("paper", broker=FakeBroker(connected=False), background=False)) as c:
        body = c.get("/api/v1/portfolio/positions").json()
        # a NaN price used to reach the JSON as null in last, market_value, weight and beta
        assert_schema(body, response_schema("/portfolio/positions", "get"))
        assert body["positions"][0]["last"] == float(good)
        assert_schema(c.get("/api/v1/risk").json(), response_schema("/risk", "get"))


def test_a_positions_file_cut_short_mid_write_still_reads(saved, frozen_now) -> None:  # noqa: ARG001
    saved.write_text("symbol,current_shares,avg_cost,sec_type,multiplier,market_price\nSPY,400,600.0,STK,1.0,612.0\nGL")
    rows, _ = history.positions_snapshot("paper")
    assert [r["symbol"] for r in rows] == ["SPY"]
    with TestClient(create_app("paper", broker=FakeBroker(connected=False), background=False)) as c:
        assert c.get("/api/v1/status").status_code == 200
        assert c.get("/api/v1/portfolio/positions").status_code == 200
