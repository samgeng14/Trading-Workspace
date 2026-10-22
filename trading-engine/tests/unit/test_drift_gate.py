"""The drift gate compares today's targets with the account's positions now, so a
name the model dropped, or one that drifted past the band, fires a rebalance."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production import daily_runner  # noqa: E402

NAV = 1_000_000.0
RB = {
    "cadence": "drift", "drift_threshold": 0.05, "composition_threshold": 0.05, "cooldown_days": 3,
    "state_path": "data/broker/state/last_rebalance.json",
}  # fmt: skip
MODEL = {"SPY": 0.2, "QQQ": 0.2, "TLT": 0.2, "GLD": 0.2, "BIL": 0.2}
POSITION_COLUMNS = ["symbol", "current_shares", "avg_cost", "sec_type", "multiplier", "market_price"]


def targets(weights: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"rebalance_date": date(2026, 10, 12), "symbol": s, "target_weight": w, "close": 100.0} for s, w in weights.items()]
    )


def positions(**held: float) -> pd.DataFrame:
    """Share positions at $100, as the snapshot (or IBKR) reports them."""
    rows = [
        {"symbol": s, "current_shares": q, "avg_cost": 100.0, "sec_type": "STK", "multiplier": 1.0, "market_price": 100.0}
        for s, q in held.items()
    ]
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


def last_submission(root: Path, *, basket: dict[str, float], on: date) -> None:
    path = root / RB["state_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"submitted_at": on.isoformat(), "basket": basket}))


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 10, 13))
    last_submission(tmp_path, basket=MODEL, on=date(2026, 10, 1))  # out of the cooldown
    return tmp_path


def gate(targets_df, positions_df, *, nav=NAV, source="ibkr"):
    return daily_runner._needs_rebalance_drift(
        "paper", RB, targets_df=targets_df, positions=positions_df, nav=nav, source=source
    )


def test_a_book_on_target_holds(root: Path) -> None:
    decided, diag = gate(targets(MODEL), positions(**{s: 2_000 for s in MODEL}))
    assert decided is False and "within band" in diag["reason"]
    assert diag["positions_from"] == "ibkr" and diag["nav"] == NAV and diag["max_drift"] == 0.0


def test_a_name_the_targets_dropped_fires(root: Path) -> None:
    # the account still holds TLT; today's targets moved its weight to QQQ
    today = {**MODEL, "QQQ": 0.4}
    del today["TLT"]
    decided, diag = gate(targets(today), positions(**{s: 2_000 for s in MODEL}))
    assert decided is True and diag["composition_changed"] is True
    assert diag["drift_per_symbol"]["TLT"] == pytest.approx(0.2)


def test_drift_past_the_band_fires(root: Path) -> None:
    held = {s: 2_000 for s in MODEL}
    held.update(SPY=2_700, BIL=1_300)  # SPY at 27%, BIL at 13%
    decided, diag = gate(targets(MODEL), positions(**held))
    assert decided is True and diag["max_drift"] == pytest.approx(0.07)
    assert "exceeds band" in diag["reason"]


def test_the_cooldown_holds_a_drift(root: Path) -> None:
    last_submission(root, basket=MODEL, on=date(2026, 10, 12))  # yesterday's orders may still be working
    held = {s: 2_000 for s in MODEL}
    held.update(SPY=2_700, BIL=1_300)
    decided, diag = gate(targets(MODEL), positions(**held))
    assert decided is False and diag["in_cooldown"] is True


def test_no_positions_or_nav_is_a_cold_start(root: Path) -> None:
    decided, diag = gate(targets(MODEL), None, nav=None, source="none")
    assert decided is True and diag["cold_start"] is True
    decided, diag = gate(targets(MODEL), positions(SPY=1), nav=None, source="snapshot")
    assert decided is True and diag["cold_start"] is True


def test_a_future_the_basket_cannot_sell_is_not_drift(root: Path) -> None:
    pos = positions(**{s: 2_000 for s in MODEL})
    mes = {"symbol": "MESZ6", "current_shares": -5, "avg_cost": 33_000.0, "sec_type": "FUT", "multiplier": 5.0,
           "market_price": 6650.0}  # fmt: skip
    pos = pd.concat([pos, pd.DataFrame([mes])], ignore_index=True)
    decided, diag = gate(targets(MODEL), pos)
    assert decided is False and "MESZ6" not in diag["drift_per_symbol"]


def test_a_holding_without_a_price_is_left_out_with_a_warning(root: Path, caplog) -> None:
    pos = positions(**{s: 2_000 for s in MODEL})
    pos.loc[pos["symbol"] == "GLD", "market_price"] = float("nan")  # no mark, and no close anywhere
    today = targets(MODEL)
    today.loc[today["symbol"] == "GLD", "close"] = 0.0
    decided, diag = gate(today, pos)
    assert decided is True and diag["drift_per_symbol"]["GLD"] == pytest.approx(0.2)
    assert "No price for GLD" in caplog.text


def test_holdings_outside_the_model_are_reported_from_the_same_positions(root: Path) -> None:
    out = daily_runner._holdings_outside_model(targets(MODEL), positions(**{s: 2_000 for s in MODEL}, XLE=100), "ibkr")
    assert out["sell"] == ["XLE"] and out["kept"] == [] and out["positions_from"] == "ibkr"
    assert daily_runner._holdings_outside_model(targets(MODEL), None, "none")["positions_from"] == "none"
