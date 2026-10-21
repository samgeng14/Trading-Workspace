"""src/strategies/trend.py is S1's rule as researched: the same weights and the same
chained prices as research/s1_trend/s1_trend.py, which stays as it was run."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.costs")
from src.strategies import trend  # noqa: E402

SYMBOLS = [*trend.RISKY, trend.CASH]


def research():
    spec = importlib.util.spec_from_file_location("s1_trend", PROJECT_ROOT / "research" / "s1_trend" / "s1_trend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic(rows: int = 420, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-02", periods=rows)
    drift = rng.normal(0, 0.0006, len(SYMBOLS))
    steps = rng.normal(drift, 0.012, (rows, len(SYMBOLS)))
    panel = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=days, columns=SYMBOLS)
    panel.loc[: days[rows * 5 // 7], "UNG"] = np.nan  # a late listing: no history until then
    return panel


@pytest.mark.parametrize("long_flat", [True, False])
def test_the_weights_are_the_researched_rules(long_flat) -> None:
    rule = research().s1("blend", "long_flat" if long_flat else "long_short")
    ours = trend.trend_rule(long_flat=long_flat)
    panel = synthetic()
    decided = 0
    for end in range(250, len(panel), 17):
        history = panel.iloc[:end]
        assert ours(history) == rule(history), history.index[-1]
        decided += ours(history) is not None
    assert decided > 5


def test_the_prices_are_chained_as_researched(tmp_path, monkeypatch) -> None:
    panel = synthetic(rows=300)
    master = panel.stack().rename("close").reset_index().rename(columns={"level_0": "date", "level_1": "symbol"})
    master["volume"] = 1_000_000.0
    master.to_parquet(tmp_path / "etf_prices_master.parquet")
    adjusted = panel.iloc[150:] * 1.01  # dividend-adjusted closes from mid-way, as IBKR's start in 2016
    tr = adjusted.stack().rename("adj_close").reset_index().rename(columns={"level_0": "date", "level_1": "symbol"})
    tr.to_parquet(tmp_path / "etf_total_return_closes.parquet")
    module = research()
    monkeypatch.setattr(module, "PRICES", tmp_path)
    theirs, their_volumes, _ = module.load_panel()
    ours, our_volumes = trend.price_panel(tmp_path)
    pd.testing.assert_frame_equal(ours, theirs)
    pd.testing.assert_frame_equal(our_volumes, their_volumes)
