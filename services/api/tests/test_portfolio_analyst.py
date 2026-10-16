"""Importing daily NAV from a PortfolioAnalyst CSV export (a made-up file in the export's layout)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dv02_api import flex, portfolio_analyst

HEAD = (
    "Introduction,Header,Name,Account,Alias,BaseCurrency,AccountType,AnalysisPeriod,PerformanceMeasure\n"
    'Introduction,Data,Test Person,{account},,USD,Individual,"March 6, 2026 to October 12, 2026 ({freq})",TWR\n'
    "Concentration,Header,Date,Symbol,Weight\n"
    "Concentration,Data,20260306,SPY,0.5\n"
    'Allocation by Asset Class,MetaInfo,Analysis Period,"March 6, 2026 - October 12, 2026"\n'
    "Allocation by Asset Class,Header,Date,Commodities,Equities,Fixed Income,Cash,NAV\n"
)
DAILY = (
    "Allocation by Asset Class,Data,20260306,0,0,0,1000086.35,1000086.35\n"
    "Allocation by Asset Class,Data,20260309,39320.26,917683.99,49955.3,5753.505,1012713.055\n"
    "Allocation by Asset Class,Data,20260415,38111.11,939881.12,49489.08,12936.743245,1040418.053245\n"
)
MONTHLY = "Allocation by Asset Class,Data,202603,38738.5,873550.26,49488.8,12774.023245,974551.583245\n"


def export(tmp_path: Path, body: str, account: str = "DU1234567", freq: str = "Daily") -> Path:
    path = tmp_path / "export.csv"
    path.write_text(HEAD.format(account=account, freq=freq) + body)
    return path


def test_reads_daily_nav_and_the_account(tmp_path: Path) -> None:
    e = portfolio_analyst.parse(export(tmp_path, DAILY))
    assert e.account == "DU1234567"
    assert e.navs[0] == (date(2026, 3, 6), 1000086.35)
    assert [d for d, _ in e.navs] == [date(2026, 3, 6), date(2026, 3, 9), date(2026, 4, 15)]


def test_a_monthly_export_says_to_choose_daily(tmp_path: Path) -> None:
    with pytest.raises(portfolio_analyst.ExportError, match="Daily"):
        portfolio_analyst.parse(export(tmp_path, MONTHLY, freq="Monthly"))


def test_not_an_export(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("date,nav\n2026-03-06,1\n")
    with pytest.raises(portfolio_analyst.ExportError, match="PortfolioAnalyst"):
        portfolio_analyst.parse(path)


def test_merge_fills_the_start_and_keeps_flex_where_both_have_a_day(engine_tree, tmp_path: Path) -> None:
    flex._write_cache("paper", {date(2026, 4, 15): 1040418.00, date(2026, 10, 9): 1050216.0})
    days, added, first = portfolio_analyst.merge("paper", portfolio_analyst.parse(export(tmp_path, DAILY)))
    assert (days, added, first) == (3, 2, date(2026, 3, 6))
    cache = dict(flex.read_cache("paper"))
    assert cache[date(2026, 3, 6)] == pytest.approx(1000086.35)
    assert cache[date(2026, 4, 15)] == pytest.approx(1040418.00)  # Flex's own value
    assert cache[date(2026, 10, 9)] == pytest.approx(1050216.0)


def test_cli_imports_and_refuses_a_live_account_under_paper(engine_tree, tmp_path: Path, capsys) -> None:
    assert portfolio_analyst.main([str(export(tmp_path, DAILY))]) == 0
    out = capsys.readouterr().out
    assert "3 days of NAV for DU1234567, 2026-03-06 to 2026-04-15" in out
    assert "Test Person" not in out

    assert portfolio_analyst.main([str(export(tmp_path, DAILY, account="U7654321"))]) == 1
    assert "live account" in capsys.readouterr().err


def test_the_curve_starts_on_the_accounts_first_day(engine_tree, tmp_path: Path, monkeypatch, frozen_now) -> None:  # noqa: ARG001
    """With no pinned inception and no older records, /performance since inception starts on the
    export's first day once it is imported (and not before)."""
    from fastapi.testclient import TestClient

    from dv02_api.app import create_app
    from tests.conftest import FakeBroker

    monkeypatch.delenv("DV02_INCEPTION_DATE", raising=False)
    monkeypatch.delenv("DV02_INCEPTION_NAV", raising=False)
    (engine_tree / "artifacts" / "api" / "nav_paper.csv").unlink()  # the fixture's own close records
    flex._write_cache("paper", {date(2026, 4, 15): 1040418.0, date(2026, 9, 30): 1040119.5})

    def start() -> str:
        with TestClient(create_app("paper", FakeBroker(), background=False)) as c:
            return c.get("/api/v1/performance", params={"range": "ITD"}).json()["start"]

    assert start() == "2026-04-15"
    portfolio_analyst.main([str(export(tmp_path, DAILY))])
    assert start() == "2026-03-06"
