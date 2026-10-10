"""Calendar, stats, Flex parsing, NAV merging, order mapping and the IBKR snapshot mapping."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from dv02_api import flex, history, nyse, orders, performance, stats
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
    navs, sources = history.nav_history("paper")
    assert dict(navs)[date(2025, 10, 16)] == 999_999.0
    assert "IBKR Flex" in sources and "recorded closes" in sources
    assert navs[0][0] == date(2025, 10, 15)


def test_inception_capital_fills_a_missing_start(engine_tree) -> None:
    (engine_tree / "artifacts" / "api" / "nav_paper.csv").write_text("date,nav\n2026-10-08,1020000\n")
    navs, sources = history.nav_history("paper")
    assert navs[0] == (date(2025, 10, 15), 1_000_000.0)
    assert any(s.startswith("inception capital") for s in sources)


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
