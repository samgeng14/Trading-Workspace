"""Every dataset's watermark against what should exist by now, and the catch-up that
fills the ones behind (src/data/datasets.py; docs/PLAN.md §11)."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data import datasets as ds  # noqa: E402

NY = ZoneInfo("America/New_York")


def ny(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY)


@pytest.mark.parametrize(("now", "through"), [
    (ny("2026-10-21", "15:00"), date(2026, 10, 19)),  # before the run: yesterday's run, through the day before
    (ny("2026-10-21", "16:05"), date(2026, 10, 20)),  # after it
    (ny("2026-10-24", "12:00"), date(2026, 10, 22)),  # a Saturday: Friday's run
    (ny("2026-11-27", "16:05"), date(2026, 11, 27)),  # an early close: the run comes after it, so the day itself
])  # fmt: skip
def test_what_the_daily_run_should_have_appended(now, through) -> None:
    assert ds.after_run(now) == through


def test_when_other_datasets_are_due() -> None:
    assert ds.statement_due(ny("2026-10-21", "13:00")) == date(2026, 10, 20)  # Flex: by noon the next day
    assert ds.statement_due(ny("2026-10-21", "10:00")) == date(2026, 10, 19)
    assert ds.statement_due(ny("2026-10-26", "10:00")) == date(2026, 10, 23)  # Friday's was due Saturday
    assert ds.latest_session_at(ny("2026-10-21", "17:30"), ds.ARCHIVE_DONE) == date(2026, 10, 20)
    assert ds.latest_session_at(ny("2026-10-21", "17:50"), ds.ARCHIVE_DONE) == date(2026, 10, 21)
    assert ds.sessions_between(date(2026, 10, 15), date(2026, 10, 21)) == [
        date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]  # fmt: skip


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    prices = tmp_path / "data" / "market" / "cleaned" / "prices"
    prices.mkdir(parents=True)
    pd.DataFrame({"date": pd.to_datetime(["2026-10-19", "2026-10-20"]), "symbol": "SPY", "close": 1.0}).to_parquet(
        prices / "etf_prices_master.parquet")  # fmt: skip
    pd.DataFrame({"date": pd.to_datetime(["2026-10-16"]), "symbol": "SPY", "adj_close": 1.0}).to_parquet(
        prices / "etf_total_return_closes.parquet")  # fmt: skip
    api = tmp_path / "artifacts" / "api"
    api.mkdir(parents=True)
    (api / "nav_paper.csv").write_text("date,nav\n2026-10-20,1.0\n2026-10-21,1.0\n")
    doctor = tmp_path / "artifacts" / "doctor"
    doctor.mkdir(parents=True)
    (doctor / "doctor_paper_2026-10-21.json").write_text("{}")
    return tmp_path


def test_each_datasets_state(tree) -> None:
    rows = {s.dataset.name: s for s in ds.status(ny("2026-10-21", "18:00"), root=tree)}
    assert rows["etf_prices"].state == "ok" and rows["etf_prices"].detail == "through 2026-10-20"
    assert rows["total_return"].state == "behind"
    assert rows["total_return"].detail == "through 2026-10-16; should be through 2026-10-20 (2 sessions behind)"
    assert rows["close_nav"].state == "ok" and rows["doctor"].state == "ok"
    assert rows["futures_curves"].state == "missing" and rows["flex_nav"].state == "missing"
    assert rows["store_close"].state == "off"  # no store on this machine
    doc = rows["total_return"].as_dict()
    assert doc["through"] == "2026-10-16" and doc["expected"] == "2026-10-20" and doc["rows"] == 1 and doc["can_catch_up"]


def test_the_adjusted_closes_list_what_the_check_of_ibkr_s_bad_days_found(tree, monkeypatch) -> None:
    prices = tree / "data" / "market" / "cleaned" / "prices"
    days = pd.bdate_range("2026-01-02", "2026-10-20")
    close = pd.Series(91.5, index=range(len(days))).to_numpy()
    pd.DataFrame({"date": days, "symbol": "BIL", "close": close}).to_parquet(prices / "etf_prices_master.parquet")
    adj = close * 0.99
    adj[3] *= 1.003  # a one-day jump, long before the signals' lookback
    adj[-5] *= 1.003  # and one inside it
    pd.DataFrame({"date": days, "symbol": "BIL", "adj_close": adj}).to_parquet(prices / "etf_total_return_closes.parquet")
    row = {s.dataset.name: s for s in ds.status(ny("2026-10-21", "18:00"), root=tree)}["total_return"]
    assert row.state == "ok" and len(row.findings) == 2
    assert row.findings[0].startswith(f"inside the signals' lookback, repaired: BIL {days[-5].date()}: IBKR's adjustment jumps")
    assert row.findings[1].startswith("1 older bad day in IBKR's series, repaired in the signals")
    assert row.as_dict()["findings"] == list(row.findings)
    assert row.findings[0] in ds.render([row], ny("2026-10-21", "18:00"))
    # a check that fails is a finding, never a crash; a dataset with nothing here has none
    monkeypatch.setattr("src.production.pipeline.append_total_return.check_stored", lambda path: 1 / 0)
    row = {s.dataset.name: s for s in ds.status(ny("2026-10-21", "18:00"), root=tree)}["total_return"]
    assert row.findings == ("its content check failed: ZeroDivisionError: division by zero",)
    (prices / "etf_total_return_closes.parquet").unlink()
    row = {s.dataset.name: s for s in ds.status(ny("2026-10-21", "18:00"), root=tree)}["total_return"]
    assert row.state == "missing" and row.findings == ()


def test_catchup_fills_what_is_behind_and_keeps_a_record(tree, monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def filler(name):
        def fill(ctx):
            calls.append((name, ctx["missing"].get(name)))
            return {"ok": True, "detail": f"{name} filled"}
        return fill

    keep = [d for d in ds.DATASETS if d.name in ("etf_prices", "total_return", "flex_nav")]
    monkeypatch.setattr(ds, "DATASETS", tuple(
        ds.Dataset(**{**d.__dict__, "catchup": filler(d.name) if d.catchup else None}) for d in keep))  # fmt: skip
    record = ds.catchup(ny("2026-10-21", "18:00"), root=tree)
    assert calls == [("total_return", [date(2026, 10, 19), date(2026, 10, 20)])]  # only what is behind
    assert record["actions"] == [
        {"dataset": "total_return", "ok": True, "detail": "total_return filled"},
        {"dataset": "flex_nav", "ok": False, "detail": "no catch-up: " + keep[2].note},
    ]  # fmt: skip
    assert record["ok"] is False  # Flex can't be caught up here
    (kept,) = (tree / "artifacts" / "catchup").glob("catchup_paper_*.json")
    assert json.loads(kept.read_text())["before"][1]["state"] == "behind"
    assert ds.last_catchup(tree)["actions"][0]["dataset"] == "total_return"


def test_no_catchup_while_the_daily_jobs_write() -> None:
    out = ds.catchup(ny("2026-10-21", "15:40"))
    assert out["ok"] is False and out["refused"].startswith("between 15:10 and 17:45 ET")
    assert ds.busy(ny("2026-10-24", "15:40")) is False  # a Saturday
