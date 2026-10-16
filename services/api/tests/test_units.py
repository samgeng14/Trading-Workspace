"""Calendar, stats, Flex parsing, NAV merging, order mapping, the IBKR snapshot mapping and the
instrument table."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from dv02_api import flex, history, nyse, orders, performance, stats, universe
from dv02_api.broker import Broker

from .conftest import fake_trade


def test_sessions_follow_the_nyse_calendar() -> None:
    at = lambda s: datetime.fromisoformat(s)  # noqa: E731
    s = nyse.session_state(at("2026-10-09T14:12:00-04:00"))
    assert (s.session, s.next_change) == ("open", "closes")
    s = nyse.session_state(at("2026-10-10T12:00:00-04:00"))  # Saturday
    assert s.session == "closed" and s.trading_date == date(2026, 10, 9)
    assert s.next_change_at == at("2026-10-12T09:30:00-04:00")
    s = nyse.session_state(at("2026-11-27T13:30:00-05:00"))  # day after Thanksgiving closes 13:00
    assert s.session == "post"
    assert not nyse.is_trading_day(date(2026, 12, 25))


def test_sharpe_interval_shrinks_with_more_data() -> None:
    short = stats.sharpe([0.001, -0.0005, 0.0012, 0.0003] * 63)
    long = stats.sharpe([0.001, -0.0005, 0.0012, 0.0003] * 252)
    assert short["ci_high"] - short["ci_low"] > long["ci_high"] - long["ci_low"]
    assert stats.sharpe([])["years"] == 0


def test_monthly_returns_chain_to_the_total() -> None:
    days = nyse.trading_days(date(2026, 1, 2), date(2026, 3, 31))
    rows = [(d, 100 * (1.001**i), 50 * (1.0005**i)) for i, d in enumerate(days)]
    months = performance.monthly(rows)
    assert [m["month"] for m in months] == [1, 2, 3]
    total = 1.0
    for m in months:
        total *= 1 + m["portfolio"]
    assert total - 1 == pytest.approx(rows[-1][1] / rows[0][1] - 1)


@pytest.mark.parametrize("rng", ["3M", "ITD"])
def test_series_nav_is_the_nav_history(engine_tree, rng: str) -> None:
    h = history.nav_history("paper", live=(date(2026, 10, 9), 1_031_250.0))
    by_day = dict(h.navs)
    p = performance.performance(h.navs, history.benchmark_levels("SPY"), rng, {"id": "SPY"})
    assert len(p["series"]) > 20 and p["series"][-1]["nav"] == 1_031_250.0
    nav0 = p["series"][0]["nav"]
    for pt in p["series"]:
        assert pt["nav"] == by_day[date.fromisoformat(pt["date"])]
        # no deposits or withdrawals in this record, so growth of $100 is NAV rebased to 100
        assert pt["portfolio"] == pytest.approx(100 * pt["nav"] / nav0)
    assert p["stats"]["total_return"] == pytest.approx(p["series"][-1]["nav"] / nav0 - 1)


FLEX_XML = b"""<FlexQueryResponse queryName="DV02 NAV" type="AF"><FlexStatements count="1">
<FlexStatement accountId="DU1234567" fromDate="20251015" toDate="20251017">
<EquitySummaryInBase>
<EquitySummaryByReportDateInBase accountId="DU1234567" reportDate="20251015" cash="1000000" total="1000000" />
<EquitySummaryByReportDateInBase accountId="DU1234567" reportDate="20251016" cash="20000" total="1003500.5" />
<EquitySummaryByReportDateInBase accountId="DU1234567" reportDate="2025-10-17" cash="20000" total="998750.25" />
</EquitySummaryInBase></FlexStatement></FlexStatements></FlexQueryResponse>"""


def test_flex_nav_parsing() -> None:
    assert flex.parse_nav(FLEX_XML) == [
        (date(2025, 10, 15), 1_000_000.0),
        (date(2025, 10, 16), 1_003_500.5),
        (date(2025, 10, 17), 998_750.25),
    ]


def test_flex_wins_over_recorded_closes(engine_tree) -> None:
    path = flex.cache_path("paper")
    path.write_text("date,nav\n2025-10-16,999999.00\n")
    h = history.nav_history("paper")
    assert dict(h.navs)[date(2025, 10, 16)] == 999_999.0
    assert "IBKR Flex" in h.sources and "recorded closes" in h.sources
    assert h.navs[0][0] == date(2025, 10, 15) and h.complete


def test_a_configured_start_fills_a_missing_inception(engine_tree) -> None:
    (engine_tree / "artifacts" / "api" / "nav_paper.csv").write_text("date,nav\n2026-10-08,1020000\n")
    h = history.nav_history("paper")
    assert h.navs[0] == (date(2025, 10, 15), 1_000_000.0)
    assert any(s.startswith("configured start") for s in h.sources) and h.complete


def test_without_a_configured_start_inception_is_the_first_real_nav(engine_tree, monkeypatch) -> None:
    monkeypatch.delenv("DV02_INCEPTION_DATE")
    (engine_tree / "artifacts" / "api" / "nav_paper.csv").write_text("date,nav\n2026-10-08,1020000\n")
    h = history.nav_history("paper", live=(date(2026, 10, 9), 1_025_000.0))
    assert h.navs == [(date(2026, 10, 8), 1_020_000.0), (date(2026, 10, 9), 1_025_000.0)]
    assert not h.complete and h.coverage()["first_date"] == "2026-10-08"


def test_period_returns_need_history_that_reaches_the_period_start() -> None:
    navs = [(date(2026, 10, 5), 100.0), (date(2026, 10, 9), 103.0)]
    assert performance.period_return(navs, date(2026, 9, 30), complete=False) is None
    # a complete history that starts mid-month means the account itself started then
    assert performance.period_return(navs, date(2026, 9, 30), complete=True) == pytest.approx(0.03)
    assert performance.period_return(navs, date(2026, 10, 6), complete=False) == pytest.approx(0.03)


def test_order_mapping() -> None:
    o = orders.from_trade(fake_trade(5, "QQQ", "SELL", 20, "Submitted", filled=5, order_type="MOC"), "ETF momentum")
    assert (o["id"], o["side"], o["status"], o["order_type"], o["filled"]) == ("5", "SELL", "partial", "MOC", 5.0)
    assert orders.from_trade(fake_trade(6, "QQQ", "BUY", 1, "Inactive"), "x")["status"] == "rejected"


class _FakeIB:
    """Just enough of ib_async.IB for Broker.snapshot()."""

    def __init__(self) -> None:
        stk = SimpleNamespace(conId=1, symbol="SPY", localSymbol="SPY", secType="STK", multiplier="")
        fut = SimpleNamespace(conId=2, symbol="MES", localSymbol="MESZ6", secType="FUT", multiplier="5")
        self._portfolio = [
            SimpleNamespace(
                contract=stk,
                position=100,
                marketPrice=600.0,
                marketValue=60_000.0,
                averageCost=550.0,
                unrealizedPNL=5_000.0,
            ),  # fmt: skip
            SimpleNamespace(
                contract=fut,
                position=-2,
                marketPrice=6600.0,
                marketValue=-66_000.0,
                averageCost=33_250.0,
                unrealizedPNL=500.0,
            ),  # fmt: skip
        ]

    def isConnected(self) -> bool:  # noqa: N802
        return True

    def accountValues(self, account: str):  # noqa: N802
        return [
            SimpleNamespace(tag="NetLiquidation", value="1001000", currency="USD"),
            SimpleNamespace(tag="NetLiquidation", value="1001234.5", currency="BASE"),
            SimpleNamespace(tag="TotalCashValue", value="941234.5", currency="BASE"),
        ]

    def portfolio(self, account: str):
        return self._portfolio


def test_broker_snapshot_maps_ibkr_objects() -> None:
    b = Broker("paper")
    b._ib = _FakeIB()
    b.account = "DU1234567"
    b.status.connected = True
    b._pnl = SimpleNamespace(dailyPnL=1234.0)
    tick = SimpleNamespace(last=601.0, close=598.0, marketDataType=3, marketPrice=lambda: 601.0)
    b._position_lines = {1: SimpleNamespace(ticker=tick)}
    b._pnl_single = {1: SimpleNamespace(value=60_100.0, dailyPnL=300.0)}
    snap = b.snapshot()
    assert snap is not None
    assert snap.nav == 1_001_234.5 and snap.cash == 941_234.5 and snap.day_pnl == 1234.0
    spy, mes = snap.positions
    assert (spy.sec_type, spy.last, spy.prev_close) == ("ETF", 601.0, 598.0)
    assert (spy.market_value, spy.day_pnl) == (60_100.0, 300.0)
    assert (mes.symbol, mes.multiplier, mes.avg_cost) == ("MESZ6", 5.0, 6650.0)
    assert mes.market_value == pytest.approx(-2 * 6600.0 * 5)  # notional, from the portfolio price
    assert b.market_data_type() == 3


def test_broker_ignores_ibkrs_minus_one_no_quote() -> None:
    """Outside market hours delayed data can carry -1 for last, bid/ask and close. It
    is "no quote", not a price: the position falls back to IBKR's own mark, and the
    ticker strip leaves the line out rather than showing a -100% day."""
    b = Broker("paper")
    b._ib = _FakeIB()
    b.account = "DU1234567"
    b.status.connected = True
    b._pnl = SimpleNamespace(dailyPnL=-50.0)
    tick = SimpleNamespace(last=-1.0, close=-1.0, marketDataType=3, marketPrice=lambda: -1.0)
    b._position_lines = {1: SimpleNamespace(ticker=tick)}
    b._pnl_single = {1: SimpleNamespace(value=60_000.0, dailyPnL=-566.0)}
    spy, _ = b.snapshot().positions
    assert spy.last == 600.0  # the portfolio mark, not -1
    assert spy.prev_close is None
    assert spy.market_value == pytest.approx(60_000.0)
    assert spy.day_pnl == -566.0
    assert b.market_data_type() is None  # no line has a usable price

    b._strip_lines = [
        SimpleNamespace(symbol="UNG", label="UNG", kind="etf", ticker=tick),
        SimpleNamespace(
            symbol="SPX",
            label="S&P 500",
            kind="index",
            ticker=SimpleNamespace(last=6650.0, close=6600.0, marketDataType=3, marketPrice=lambda: 6650.0),
        ),
    ]
    quotes = b.quotes()
    assert [q["symbol"] for q in quotes] == ["SPX"]
    assert quotes[0]["change_pct"] == pytest.approx(6650 / 6600 - 1)


def test_broker_refuses_a_live_account_under_paper() -> None:
    import asyncio

    class LiveIB:
        RequestTimeout = 0

        async def connectAsync(self, *a, **k):  # noqa: N802
            return self

        def managedAccounts(self):  # noqa: N802
            return ["U7654321"]

        def disconnect(self) -> None:
            self.closed = True

        def isConnected(self) -> bool:  # noqa: N802
            return not getattr(self, "closed", False)

    import dataclasses

    b = Broker("paper", ib_factory=LiveIB)
    b.endpoint = dataclasses.replace(b.endpoint, account=None)  # ignore any account set in .env
    with pytest.raises(PermissionError, match="live account"):
        asyncio.run(b._connect())


def test_bars_staleness(engine_tree) -> None:
    history.write_bars("XYZ", [(date(2026, 10, 8), 10.0)])
    friday_evening = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)
    assert history.bars_stale("XYZ", friday_evening)
    history.write_bars("XYZ", [(date(2026, 10, 8), 10.0), (date(2026, 10, 9), 10.5)])
    assert not history.bars_stale("XYZ", friday_evening)


def test_dashboard_run_uses_the_launch_agents_python_and_env(engine_tree, tmp_path, monkeypatch) -> None:
    import plistlib
    import sys
    import time as _time

    from dv02_api import runs

    home = tmp_path / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / f"{runs.LAUNCH_AGENT}.plist").write_bytes(plistlib.dumps({
        "ProgramArguments": ["/opt/engine/bin/python", "-m", "src.production.daily_runner"],
        "EnvironmentVariables": {"CAPITALFUND_OPTIMIZER": "momentum_tilted"},
    }))  # fmt: skip
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DV02_ENGINE_PYTHON", raising=False)
    cmd, env = runs.runner_command("paper")
    assert cmd[0] == "/opt/engine/bin/python" and "--force-rebalance" in cmd
    assert env["CAPITALFUND_OPTIMIZER"] == "momentum_tilted"
    assert cmd[cmd.index("--client-id") + 1] == "310"

    # a stand-in interpreter that records its arguments instead of trading
    record = tmp_path / "args.txt"
    fake = tmp_path / "fakepy"
    fake.write_text(f"#!{sys.executable}\nimport sys; open({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    fake.chmod(0o755)
    monkeypatch.setenv("DV02_ENGINE_PYTHON", str(fake))
    assert runs.start_daily_run("paper") is True
    assert runs.start_daily_run("paper") is False  # one at a time
    deadline = _time.monotonic() + 10
    while runs.RUN_STATE["running"] and _time.monotonic() < deadline:
        _time.sleep(0.05)
    assert runs.RUN_STATE["result"]["ok"] is True
    assert record.read_text().startswith("-m src.production.daily_runner --profile paper")


def test_a_failed_setup_releases_the_client_id() -> None:
    """If subscriptions fail after the socket connects, the session must be closed so the loop can retry."""
    import asyncio
    import dataclasses

    made = []

    class FlakyIB:
        RequestTimeout = 0

        def __init__(self) -> None:
            self.closed = False
            made.append(self)

        async def connectAsync(self, *a, **k):  # noqa: N802
            return self

        def managedAccounts(self):  # noqa: N802
            return ["DU1234567"]

        def reqMarketDataType(self, t):  # noqa: N802
            raise RuntimeError("boom")

        def disconnect(self) -> None:
            self.closed = True

        def isConnected(self) -> bool:  # noqa: N802
            return not self.closed

    b = Broker("paper", ib_factory=FlakyIB)
    b.endpoint = dataclasses.replace(b.endpoint, account=None)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(b._connect())
    assert made[0].closed and not b.connected


def test_the_model_list_is_the_engines_model_universe() -> None:
    from src.production.generate_targets import model_universe

    symbols = [i.symbol for i in universe.MODEL]
    assert len(symbols) == 21 and set(symbols) == set(model_universe())
    # TQQQ left the model, but the account holds it until the next rebalance sells it
    assert "TQQQ" not in {i.symbol for i in universe.MODEL}
    assert universe.lookup("TQQQ").name == "ProShares UltraPro QQQ"


def test_leverage_agrees_with_the_no_leverage_checks_exposure_table() -> None:
    # beta and the risk scenarios use Instrument.leverage; the C++ check uses EXPOSURE
    from src.runtime.leverage import EXPOSURE, exposure_of

    for symbol in set(universe.BY_SYMBOL) | set(EXPOSURE):
        assert universe.lookup(symbol).leverage == exposure_of(symbol), symbol
    assert universe.lookup("TQQQ").leverage == 3.0


def test_a_skipped_run_says_why() -> None:
    from dv02_api import runs

    doc = {"ok": True, "skipped": True, "reason": "NYSE closed: a market holiday"}
    assert runs._summary(doc) == "Skipped: NYSE closed: a market holiday"
    assert runs._summary({"ok": True}) == "OK"
