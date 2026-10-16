"""The daily run is scheduled before the closing auctions stop taking orders, and the
dashboard says how its orders execute: market-on-close until 15:45 ET, market-on-open
for the next session after that. The engine decides (src/broker/ibkr/auction.py);
these check what the schedule panel and the rebalance preview say about it."""

from __future__ import annotations

import os
import time as systime
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from dv02_api import nyse, rebalance, runs
from dv02_api.app import create_app

CT = ZoneInfo("America/Chicago")


@pytest.fixture()
def zone(monkeypatch: pytest.MonkeyPatch):
    """``zone(name)`` sets this process's local time zone, as the Mac's; restored after."""
    monkeypatch.delenv("DV02_RUNNER_TIME", raising=False)
    old = os.environ.get("TZ")

    def set_zone(name: str) -> None:
        os.environ["TZ"] = name
        systime.tzset()

    yield set_zone
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    systime.tzset()


# ── the schedule ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "local"),
    [("America/Chicago", time(14, 35)), ("America/New_York", time(15, 35)), ("UTC", time(19, 35))],
)
def test_the_run_is_installed_at_1535_et_in_the_macs_time(zone, name: str, local: time) -> None:
    zone(name)
    assert runs.runner_time(date(2026, 10, 13)) == local


def test_central_and_eastern_keep_1535_et_through_daylight_saving_time(zone) -> None:
    for name, local in (("America/Chicago", time(14, 35)), ("America/New_York", time(15, 35))):
        zone(name)
        assert {runs.runner_time(date(2026, 1, 13)), runs.runner_time(date(2026, 7, 14))} == {local}


def test_dv02_runner_time_still_overrides_it(zone, monkeypatch: pytest.MonkeyPatch) -> None:
    zone("America/Chicago")
    monkeypatch.setenv("DV02_RUNNER_TIME", "14:20")
    assert runs.runner_time() == time(14, 20)


def plist(hour: int, minute: int) -> dict:
    return {"StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)]}


def job(now: datetime) -> dict:
    return runs.schedule(now)[0]


def test_the_schedule_shows_1435_central_and_market_on_close(engine_tree, zone, monkeypatch) -> None:  # noqa: ARG001
    zone("America/Chicago")
    monkeypatch.setattr(runs, "launch_agent", lambda: None)
    j = job(datetime(2026, 10, 13, 13, 0, tzinfo=CT))
    assert j["at"] == "2026-10-13T14:35:00-05:00" and j["status"] == "scheduled"
    assert j["label"] == "Daily run: data, targets, rebalance if drift > 5%; orders go market-on-close"
    assert j["detail"] == "15:35 ET; market-on-close, filled at the Tue Oct 13 close"


def test_the_schedule_reads_the_installed_time(engine_tree, zone, monkeypatch) -> None:  # noqa: ARG001
    zone("America/Chicago")
    monkeypatch.setattr(runs, "launch_agent", lambda: plist(16, 32))  # installed before this change
    j = job(datetime(2026, 10, 13, 13, 0, tzinfo=CT))
    assert j["at"] == "2026-10-13T16:32:00-05:00"
    assert j["label"].endswith("orders go market-on-open")
    assert j["detail"] == "17:32 ET; market-on-open, filled at the Wed Oct 14 open"


def test_on_an_early_close_the_run_misses_the_close(engine_tree, zone, monkeypatch) -> None:  # noqa: ARG001
    zone("America/Chicago")
    monkeypatch.setattr(runs, "launch_agent", lambda: None)
    j = job(datetime(2026, 11, 27, 10, 0, tzinfo=CT))  # the day after Thanksgiving closes at 13:00 ET
    assert j["label"].endswith("orders go market-on-open")
    assert j["detail"] == "15:35 ET; market-on-open, filled at the Mon Nov 30 open"


def test_on_a_weekend_the_label_is_mondays_run(engine_tree, zone, monkeypatch) -> None:  # noqa: ARG001
    zone("America/New_York")
    monkeypatch.setattr(runs, "launch_agent", lambda: None)
    j = job(datetime(2026, 10, 17, 12, 0, tzinfo=nyse.ET))
    assert j["status"] == "skipped" and j["label"].endswith("orders go market-on-close")


def test_a_runs_submit_step_says_how_its_orders_went() -> None:
    step = {
        "ok": True,
        "submitted": True,
        "result": {
            "submitted": True,
            "rows": 4,
            "execution": {"summary": "market-on-close, filled at the Tue Oct 13 close"},
        },
    }
    assert runs._step_detail("submit_orders", step) == (
        True,
        "submitted 4 orders, market-on-close, filled at the Tue Oct 13 close",
    )


# ── the rebalance preview ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "text"),
    [
        (
            datetime(2026, 10, 13, 15, 0, tzinfo=nyse.ET),
            "Market-on-close through the daily runner: filled at today's close (Tue Oct 13) "
            "if the run sends them by 15:45 ET, else at the next open",
        ),
        (
            datetime(2026, 10, 13, 15, 45, tzinfo=nyse.ET),
            "Market-on-open through the daily runner: filled at the next open, Wed Oct 14",
        ),
        (
            datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc),  # 18:00 ET, closed
            "Market-on-open through the daily runner: filled at the next open, Wed Oct 14",
        ),
        (
            datetime(2026, 10, 17, 12, 0, tzinfo=nyse.ET),  # Saturday
            "Market-on-open through the daily runner: filled at the next open, Mon Oct 19",
        ),
        (
            datetime(2026, 11, 27, 12, 0, tzinfo=nyse.ET),  # early close: the deadline is 12:45
            "Market-on-close through the daily runner: filled at today's close (Fri Nov 27) "
            "if the run sends them by 12:45 ET, else at the next open",
        ),
        (
            datetime(2026, 11, 27, 12, 50, tzinfo=nyse.ET),
            "Market-on-open through the daily runner: filled at the next open, Mon Nov 30",
        ),
    ],
)
def test_the_preview_says_when_the_orders_fill(now: datetime, text: str) -> None:
    assert rebalance.execution_text(["SPY", "TLT", "GLD"], now) == text


def test_the_preview_endpoint_says_it_too(engine_tree, fake_broker, frozen_now, monkeypatch) -> None:  # noqa: ARG001
    from dv02_api import app as app_mod

    with TestClient(create_app("paper", broker=fake_broker, background=False)) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()  # Friday 14:12 ET
        assert body["execution"].startswith(
            "Market-on-close through the daily runner: filled at today's close (Fri Oct 9) "
        )
        monkeypatch.setattr(app_mod, "_now", lambda: frozen_now + timedelta(hours=1, minutes=40))  # 15:52 ET
        body = c.get("/api/v1/trading/rebalance/preview").json()
        assert body["execution"] == "Market-on-open through the daily runner: filled at the next open, Mon Oct 12"


# ── one calendar ───────────────────────────────────────────────────────


def test_the_engine_and_the_api_agree_on_the_calendar() -> None:
    from src.common import clock

    days = [date(2025, 1, 1) + timedelta(days=n) for n in range(3 * 365)]
    assert [d for d in days if clock.is_early_close(d)] == sorted(nyse.EARLY_CLOSES)
    # 2025-01-09 (a national day of mourning) is in the API's list only; it is past
    future = [d for d in days if d >= date(2026, 1, 1)]
    assert [d for d in future if clock.is_us_business_day(d)] == [d for d in future if nyse.is_trading_day(d)]
