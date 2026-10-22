"""Shadow mode for H2, S0 with a trend overlay (src/production/shadow_h2.py): the
trend sleeve run forward from the shadow's start, mixed half and half with the live
account, and what H2 would hold and send; nothing is sent."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.walkforward")
from src.production import shadow_h2  # noqa: E402
from src.strategies import trend  # noqa: E402

SYMBOLS = [*trend.RISKY, trend.CASH]
START = date(2026, 3, 2)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    rng = np.random.default_rng(3)
    days = pd.bdate_range("2025-01-02", "2026-04-30")
    steps = rng.normal(0.0004, 0.01, (len(days), len(SYMBOLS)))
    panel = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=days, columns=SYMBOLS)
    prices = tmp_path / "data" / "market" / "cleaned" / "prices"
    prices.mkdir(parents=True)
    master = panel.stack().rename("close").reset_index().rename(columns={"level_0": "date", "level_1": "symbol"})
    master["volume"] = 5_000_000.0
    master.to_parquet(prices / "etf_prices_master.parquet")
    master.rename(columns={"close": "adj_close"})[["date", "symbol", "adj_close"]].to_parquet(
        prices / "etf_total_return_closes.parquet")  # fmt: skip
    targets = tmp_path / "data" / "market" / "cleaned" / "targets"
    targets.mkdir(parents=True)
    pd.DataFrame({"rebalance_date": pd.Timestamp("2026-04-30"), "symbol": ["SPY", "TLT"],
                  "target_weight": [0.6, 0.4]}).to_parquet(targets / "etf_targets_monthly.parquet")  # fmt: skip
    api = tmp_path / "artifacts" / "api"
    api.mkdir(parents=True)
    navs = 1_000_000 * np.cumprod(1 + rng.normal(0.0003, 0.008, len(days)))
    rows = [f"{d.date()},{v:.2f}" for d, v in zip(days, navs)]
    (api / "nav_paper.csv").write_text("date,nav\n" + "\n".join(rows) + "\n")
    (api / "flex_nav_paper.csv").write_text("date,nav\n" + "\n".join(rows[:-3]) + "\n")  # Flex lags the close
    positions = tmp_path / "data" / "broker" / "positions"
    positions.mkdir(parents=True)
    pd.DataFrame({"symbol": SYMBOLS, "current_shares": 100.0, "market_price": panel.iloc[-1].to_numpy()}).to_csv(
        positions / "paper_positions_snapshot.csv", index=False)  # fmt: skip
    return tmp_path


def test_nothing_before_the_shadow_starts(tree) -> None:
    out = shadow_h2.run(root=tree, start=date(2026, 4, 30))
    assert out == {"ok": True, "skipped": "the shadow starts on 2026-04-30; prices run to 2026-04-30"}


def test_the_sleeve_runs_forward_and_mixes_half_and_half_with_the_live_account(tree) -> None:
    out = shadow_h2.run(now=datetime(2026, 4, 30, 21, 5, tzinfo=timezone.utc), root=tree, start=START)
    assert out["ok"] and out["session"] == "2026-04-30" and out["sessions"] > 30
    daily = pd.read_csv(tree / "artifacts" / "shadow" / "h2" / "daily.csv", index_col="date", parse_dates=True)
    assert daily.index[0] > pd.Timestamp(START)  # decided on the start's close, traded the next day
    mixed = 0.5 * daily["sleeve_return"] + 0.5 * daily["live_return"]
    pd.testing.assert_series_equal(daily["h2_return"], mixed, check_names=False)
    assert out["sleeve_decision"]["decided"] == "2026-04-29"  # the third, 21 sessions apart from the start's
    assert sum(out["h2_targets"].values()) == pytest.approx(trend.BUDGET, abs=1e-5)  # each rounded to 6 places
    assert out["h2_targets"]["SPY"] >= 0.5 * 0.6 * trend.BUDGET  # S0's half is there
    assert out["would_be_orders"] and all(o["quantity"] > 0 for o in out["would_be_orders"])
    kept = json.loads((tree / "artifacts" / "shadow" / "h2" / "2026-04-30.json").read_text())
    assert kept["h2_targets"] == out["h2_targets"] and kept["recorded_at"] == "2026-04-30T21:05:00+00:00"


def test_would_be_orders_take_the_account_to_the_targets() -> None:
    positions = pd.DataFrame({"symbol": ["SPY", "TLT"], "current_shares": [100.0, 50.0], "market_price": [500.0, 80.0]})
    orders = shadow_h2.would_be_orders({"SPY": 0.04, "GLD": 0.5}, positions, nav=1_000_000.0)
    assert orders == [{"symbol": "SPY", "action": "SELL", "quantity": 20, "dollars": 10_000.0},
                      {"symbol": "TLT", "action": "SELL", "quantity": 50, "dollars": 4_000.0}]  # GLD: no mark yet


def test_the_first_evening_with_one_session_past_the_start(tree) -> None:
    out = shadow_h2.run(root=tree, start=date(2026, 4, 29))
    assert out["ok"] and out["session"] == "2026-04-30"
    assert out["sleeve_decision"]["decided"] == "2026-04-29" and out["sleeve_decision"]["executed"] == "2026-04-30"
