"""The exposure limits come from config/risk_limits.yaml, every key set, nothing
unknown, and none looser than the no-leverage policy."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.portfolio.risk import exposure_limits as el  # noqa: E402
from src.runtime.risk_checks import run_pre_trade_risk_checks  # noqa: E402

GOOD = {
    "risk_limits": {
        "portfolio": {"max_gross_exposure": 1.0, "min_net_exposure": 0.5, "max_net_exposure": 1.0,
                      "min_cash_buffer": 0.0, "min_holdings": 5},
        "position": {"max_single_name_weight": 0.2, "max_single_trade_pct_of_nav": 0.3},
    }
}  # fmt: skip


def doc(**changes) -> dict:
    """GOOD with ``section__key=value`` changes; a value of None deletes the key."""
    out = copy.deepcopy(GOOD)
    for name, value in changes.items():
        section, key = name.split("__")
        if value is None:
            del out["risk_limits"][section][key]
        else:
            out["risk_limits"][section][key] = value
    return out


def test_the_committed_file_loads_and_is_the_default() -> None:
    lim = el.load_exposure_limits()
    assert (lim.max_gross_exposure_pct, lim.min_net_exposure_pct, lim.max_net_exposure_pct) == (1.0, 0.5, 1.0)
    assert (lim.max_single_position_pct, lim.min_holdings, lim.min_cash_buffer_pct) == (0.2, 5, 0.0)
    assert lim.max_single_trade_pct_of_nav == 0.3
    assert el.DEFAULT_EXPOSURE_LIMITS == lim


def test_an_unknown_key_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown settings: portfolio.max_turnover"):
        el.exposure_limits_from(doc(portfolio__max_turnover=0.5))


def test_a_missing_key_is_an_error() -> None:
    with pytest.raises(ValueError, match="position.max_single_name_weight must be a number"):
        el.exposure_limits_from(doc(position__max_single_name_weight=None))
    with pytest.raises(ValueError, match="no risk_limits section"):
        el.exposure_limits_from({})


def test_a_wrong_type_is_an_error() -> None:
    with pytest.raises(ValueError, match="portfolio.min_holdings must be a number"):
        el.exposure_limits_from(doc(portfolio__min_holdings="five"))
    with pytest.raises(ValueError, match="portfolio.max_gross_exposure must be a number"):
        el.exposure_limits_from(doc(portfolio__max_gross_exposure=True))


def test_a_looser_cap_than_the_policy_is_replaced_with_a_warning(caplog) -> None:
    lim = el.exposure_limits_from(doc(portfolio__max_gross_exposure=1.5, position__max_single_name_weight=0.3))
    assert lim.max_gross_exposure_pct == 1.0 and lim.max_single_position_pct == 0.25
    assert "max_gross_exposure = 1.5 is looser" in caplog.text and "max_single_name_weight = 0.3 is looser" in caplog.text


def test_net_bounds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="min_net_exposure is above"):
        el.exposure_limits_from(doc(portfolio__min_net_exposure=1.0, portfolio__max_net_exposure=0.5))


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        el.load_exposure_limits(tmp_path / "nowhere.yaml")


def test_the_gate_enforces_the_file_limits(tmp_path: Path, monkeypatch) -> None:
    from src.execution import kill_switch

    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "kill_switch.json")
    basket = pd.DataFrame(
        [{"symbol": s, "target_weight": w, "estimated_trade_dollars": w * 100_000, "side": "BUY"}
         for s, w in {"SPY": 0.22, "QQQ": 0.2, "TLT": 0.2, "GLD": 0.19, "BIL": 0.19}.items()]
    )  # fmt: skip
    res = run_pre_trade_risk_checks(basket_df=basket, nav=100_000.0)
    # 22% in one name: inside the policy's 25%, outside the file's 20%
    assert not res.ok and any("max_single_position: SPY = 22.0% > limit 20.0%" in r for r in res.blocking_reasons)
    basket.loc[basket["symbol"] == "SPY", "target_weight"] = 0.2
    basket.loc[basket["symbol"] == "GLD", "target_weight"] = 0.2
    res = run_pre_trade_risk_checks(basket_df=basket, nav=100_000.0)
    assert res.ok, res.blocking_reasons
