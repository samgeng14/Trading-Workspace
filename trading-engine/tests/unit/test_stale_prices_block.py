"""A model ETF whose last bar lags the last completed session blocks the run: the
targets would be built on a close the market has left behind. Any other symbol
that far behind is a warning, as before."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.cleaning.validators import validate_master_prices  # noqa: E402
from src.runtime.risk_checks import run_pre_trade_risk_checks  # noqa: E402

# five sessions; 2026-10-12 is Columbus Day, on which NYSE trades
SESSIONS = [date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]
AS_OF = date(2026, 10, 12)


def master(last: dict[str, date]) -> pd.DataFrame:
    """Flat $100 closes for each symbol on every session up to its own last date."""
    rows = [{"date": d, "symbol": s, "close": 100.0} for s, end in last.items() for d in SESSIONS if d <= end]
    return pd.DataFrame(rows)


def test_a_model_etf_two_sessions_behind_blocks() -> None:
    v = validate_master_prices(master({"SPY": AS_OF, "TLT": date(2026, 10, 8)}), model_symbols=("SPY", "TLT"), as_of=AS_OF)
    assert not v.ok
    (err,) = v.errors
    assert "TLT (last bar 2026-10-08, 2 sessions behind)" in err and "bin/tradeit refresh" in err
    assert v.summary["stale_symbols"] == 1


def test_one_session_behind_is_tolerated() -> None:
    v = validate_master_prices(master({"SPY": AS_OF, "TLT": date(2026, 10, 9)}), model_symbols=("SPY", "TLT"), as_of=AS_OF)
    assert v.ok and v.errors == [] and v.warnings == []


def test_a_symbol_outside_the_model_only_warns() -> None:
    v = validate_master_prices(master({"SPY": AS_OF, "XLE": date(2026, 10, 6)}), model_symbols=("spy",), as_of=AS_OF)
    assert v.ok and v.errors == []
    assert any("XLE" in w and "stale" in w for w in v.warnings)


def test_a_model_etf_with_no_bars_at_all_warns() -> None:
    v = validate_master_prices(master({"SPY": AS_OF}), model_symbols=("SPY", "GLD"), as_of=AS_OF)
    assert v.ok and any("GLD" in w and "no bars at all" in w for w in v.warnings)


def test_without_a_model_the_old_warning_still_stands() -> None:
    v = validate_master_prices(master({"SPY": AS_OF, "TLT": date(2026, 10, 6)}), as_of=AS_OF)
    assert v.ok and any("1 symbols are stale" in w for w in v.warnings)


def test_the_pre_trade_gate_blocks_on_a_stale_model_etf(tmp_path, monkeypatch) -> None:
    import src.production.generate_targets as generate_targets
    from src.execution import kill_switch

    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "kill_switch.json")
    monkeypatch.setattr(generate_targets, "model_universe", lambda: ("SPY", "TLT", "GLD", "IWM", "QQQ"))
    basket = pd.DataFrame(
        [{"symbol": s, "target_weight": 0.2, "estimated_trade_dollars": 20_000.0, "side": "BUY"}
         for s in ("SPY", "TLT", "GLD", "IWM", "QQQ")]
    )
    path = tmp_path / "etf_prices_master.parquet"
    fresh = {s: AS_OF for s in ("SPY", "TLT", "GLD", "IWM", "QQQ")}

    master({**fresh, "TLT": date(2026, 10, 8)}).to_parquet(path, index=False)
    res = run_pre_trade_risk_checks(basket_df=basket, nav=1_000_000.0, prices_path=path, as_of=AS_OF)
    assert not res.ok
    assert any(r.startswith("prices: ") and "TLT" in r for r in res.blocking_reasons), res.blocking_reasons

    master(fresh).to_parquet(path, index=False)
    res = run_pre_trade_risk_checks(basket_df=basket, nav=1_000_000.0, prices_path=path, as_of=AS_OF)
    assert res.ok, res.blocking_reasons
