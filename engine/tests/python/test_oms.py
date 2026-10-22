"""dv02_engine.nyse and dv02_engine.oms from Python: the bindings and their
datetime faces. trading-engine/tests/unit/test_engine_oms.py compares them
with the Python runner input for input."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from dv02_engine import nyse, oms


def test_calendar_dates_and_times_round_trip() -> None:
    assert nyse.weekday(date(2026, 10, 21)) == 2
    assert nyse.easter(2026) == date(2026, 4, 5)
    assert date(2026, 11, 26) in nyse.holidays(2026)
    assert not nyse.is_business_day(date(2026, 11, 26))
    assert nyse.is_early_close(date(2026, 11, 27))
    opens, closes = nyse.session_for(date(2026, 11, 27))
    assert (opens.hour, opens.minute, closes.hour) == (9, 30, 13)
    assert nyse.session_for(date(2026, 10, 24)) is None
    assert nyse.next_open(datetime(2026, 10, 23, 16, 30, tzinfo=nyse.NY)) == datetime(2026, 10, 26, 9, 30, tzinfo=nyse.NY)
    # 20:15 UTC on Oct 21 is 16:15 ET: the day's bar has settled
    assert nyse.last_completed_session(datetime(2026, 10, 21, 20, 15, tzinfo=timezone.utc)) == date(2026, 10, 21)
    assert nyse.previous_business_day(date(2026, 10, 26)) == date(2026, 10, 23)
    i = nyse.to_instant(datetime(2026, 10, 21, 15, 44, 59, 999999))
    assert nyse.from_instant(i) == datetime(2026, 10, 21, 15, 44, 59, 999999, tzinfo=nyse.NY)


def test_basket_sells_first_and_trims_buys_to_the_cash() -> None:
    b = oms.plan_basket(
        [oms.Target("SPY", 0.6, 500.0), oms.Target("TLT", 0.4, 100.0)],
        [oms.Holding("XLE", 10, "STK", 90.0), oms.Holding("MESZ6", 1, "FUT", 6650.0)],
        nav=100_000.0,
        cash=50_000.0,
    )
    assert b.ok and b.outside_sell == ["XLE"] and b.outside_kept == ["MESZ6"]
    assert [(o.side, o.symbol) for o in b.orders] == [("SELL", "XLE"), ("BUY", "SPY"), ("BUY", "TLT")]
    assert 0 < b.trim_keep < 1 and sum(line.trimmed_shares for line in b.lines) > 0
    assert [line.side for line in b.lines] == ["BUY", "BUY", "SELL"]


def test_basket_refuses_an_unpriced_target() -> None:
    b = oms.plan_basket([oms.Target("SPY", 0.5, math.nan)], [], nav=100_000.0)
    assert not b.ok and "SPY" in b.error


def test_drift_gate() -> None:
    d = oms.rebalance_decision({"SPY": 0.6}, {"SPY": 0.5}, date(2026, 10, 21), last_basket={"SPY": 0.6})
    assert d.rebalance and d.reason == "max drift 0.1000 exceeds band 0.0500"
    d = oms.rebalance_decision({"SPY": 0.6}, {"SPY": 0.5}, date(2026, 10, 21), last_basket={"SPY": 0.6},
                               last_submitted_at=date(2026, 10, 20))  # fmt: skip
    assert not d.rebalance and d.in_cooldown and d.cooldown_remaining_days == 2
    with pytest.raises(ValueError):
        oms.rebalance_decision({}, {}, date(2026, 10, 21), drift_threshold=-1.0)


def test_execution() -> None:
    e = oms.execution_for(["ARCA"], datetime(2026, 10, 21, 15, 44, tzinfo=nyse.NY))
    assert (e["order_type"], e["tif"], e["auction"]) == ("MOC", "DAY", "close")
    assert e["moc_deadline"] == datetime(2026, 10, 21, 15, 45, tzinfo=nyse.NY)
    e = oms.execution_for(["ARCA"], datetime(2026, 10, 21, 15, 45, tzinfo=nyse.NY))
    assert (e["order_type"], e["tif"], e["session"], e["moc_deadline"]) == ("MKT", "OPG", date(2026, 10, 22), None)


def test_pairwise_sum_is_numpy_s() -> None:
    np = pytest.importorskip("numpy")
    values = [1e16, 1.0, -1e16, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0] * 7
    assert oms.pairwise_sum(values) == float(np.sum(np.array(values)))
