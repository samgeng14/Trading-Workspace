"""The legacy dashboard's positions view (src/dashboard/routers/asset.py): a
snapshot row IBKR has no mark for is priced from the price master, row by row,
and a row with no price anywhere is shown blank, never as $0."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.dashboard.routers import asset  # noqa: E402

NAV = 100_000.0


@pytest.fixture()
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(asset, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(asset, "_autodetect_port", lambda profile: 4002)
    prices = tmp_path / "data" / "market" / "cleaned" / "prices"
    prices.mkdir(parents=True)
    pd.DataFrame(
        [
            {"date": "2026-10-08", "symbol": "TLT", "close": 85.0},
            {"date": "2026-10-09", "symbol": "TLT", "close": 86.0},
            {"date": "2026-10-09", "symbol": "SPY", "close": 600.0},
        ]
    ).to_parquet(prices / "etf_prices_master.parquet", index=False)
    positions = tmp_path / "data" / "broker" / "positions"
    positions.mkdir(parents=True)
    # IBKR marked SPY; TLT has no mark (the master has it); ZZZ has no price anywhere
    pd.DataFrame(
        [
            {"symbol": "SPY", "current_shares": 100, "avg_cost": 550.0, "sec_type": "STK", "multiplier": 1.0,
             "market_price": 612.0},
            {"symbol": "TLT", "current_shares": 200, "avg_cost": 90.0, "sec_type": "STK", "multiplier": 1.0,
             "market_price": float("nan")},
            {"symbol": "ZZZ", "current_shares": 50, "avg_cost": 10.0, "sec_type": "STK", "multiplier": 1.0,
             "market_price": float("nan")},
        ]
    ).to_csv(positions / "paper_positions_snapshot.csv", index=False)
    account = tmp_path / "data" / "broker" / "account"
    account.mkdir(parents=True)
    (account / "paper_account_summary_20261009_163210.csv").write_text(
        f"tag,value\nNetLiquidation,{NAV}\nTotalCashValue,21600\n"
    )
    return tmp_path


def test_rows_without_a_mark_are_priced_from_the_master_row_by_row(tree: Path) -> None:
    df = asset._load_positions(tree / "data" / "broker" / "positions" / "paper_positions_snapshot.csv", NAV)
    rows = df.set_index("symbol")
    assert rows.loc["SPY", "last_price"] == 612.0  # IBKR's mark, not the master's 600
    assert rows.loc["TLT", "last_price"] == 86.0  # the master's last close, not 0
    assert rows.loc["TLT", "market_value"] == pytest.approx(200 * 86.0)
    assert rows.loc["TLT", "unrealized_pnl"] == pytest.approx(200 * (86.0 - 90.0))
    assert rows.loc["TLT", "weight_pct"] == pytest.approx(17.2)
    # no price anywhere: unknown, not $0
    assert math.isnan(rows.loc["ZZZ", "last_price"]) and math.isnan(rows.loc["ZZZ", "market_value"])


def test_the_endpoint_shows_an_unpriced_row_blank(tree: Path) -> None:
    body = asset.positions("paper")
    json.dumps(body, allow_nan=False)  # what the API's JSON response requires
    rows = {r["symbol"]: r for r in body["positions"]}
    assert rows["TLT"]["last_price"] == 86.0 and rows["TLT"]["market_value"] == pytest.approx(17_200.0)
    assert rows["ZZZ"]["last_price"] is None and rows["ZZZ"]["market_value"] is None
    assert body["summary"]["long_market_value"] == pytest.approx(61_200.0 + 17_200.0)
    assert [r["symbol"] for r in body["pnl_chart"]] == ["SPY", "TLT"]
