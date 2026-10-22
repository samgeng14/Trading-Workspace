"""Target generation reads the model universe (config/universes/etfs.yaml), not
every symbol in the price master. TQQQ left the model on 2026-10-12; its prices
stay in the master."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production import generate_targets as gt  # noqa: E402


def test_the_model_universe_is_21_etfs_without_tqqq() -> None:
    model = gt.model_universe()
    assert len(model) == 21 and len(set(model)) == 21
    assert "TQQQ" not in model and "QQQ" in model


def test_the_ibkr_doctor_checks_the_same_list() -> None:
    from src.broker.ibkr import doctor

    assert doctor.default_symbols() == list(gt.model_universe())


def test_tqqq_prices_stay_in_the_master() -> None:
    master = pd.read_parquet(PROJECT_ROOT / "data" / "market" / "cleaned" / "prices" / "etf_prices_master.parquet")
    assert "TQQQ" in set(master["symbol"])


def test_a_universe_file_without_etfs_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "etfs.yaml"
    path.write_text("etfs:\n  core: []\n")
    with pytest.raises(ValueError, match="no etfs.core list"):
        gt.model_universe(path)


@pytest.fixture()
def master(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A two-year price master holding every model ETF plus TQQQ and a stray symbol."""
    dates = pd.bdate_range("2024-09-02", "2026-10-09")
    rng = np.random.default_rng(7)
    rows = []
    # TQQQ gets the best trend in the panel, so any optimizer that saw it would want it
    for i, sym in enumerate([*gt.model_universe(), "TQQQ", "XYZ"]):
        drift = 0.003 if sym == "TQQQ" else 0.0002 * (i % 5)
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(dates))))
        rows.append(pd.DataFrame({"date": dates, "symbol": sym, "close": close}))
    paths = {
        "prices_csv": tmp_path / "etf_prices_master.csv",
        "prices_parquet": tmp_path / "etf_prices_master.parquet",
        "targets_csv": tmp_path / "targets" / "etf_targets_monthly.csv",
        "targets_parquet": tmp_path / "targets" / "etf_targets_monthly.parquet",
    }
    pd.concat(rows, ignore_index=True).to_parquet(paths["prices_parquet"], index=False)
    monkeypatch.setattr(gt, "_paths", lambda: paths)
    return paths


@pytest.mark.parametrize("optimizer", ["ensemble_default", "inverse_vol", "momentum_top5"])
def test_targets_never_include_tqqq(master: dict[str, Path], optimizer: str) -> None:
    result = gt.generate_targets(optimizer=optimizer)
    targets = pd.read_parquet(master["targets_parquet"])
    assert not targets.empty
    assert "TQQQ" not in set(targets["symbol"]) and "XYZ" not in set(targets["symbol"])
    assert set(targets["symbol"]) <= set(gt.model_universe())
    assert result["symbols_considered"] == 21 and result["universe_missing"] == []
    latest = targets.loc[targets["rebalance_date"] == targets["rebalance_date"].max()]
    assert latest["target_weight"].sum() == pytest.approx(1.0)


def test_a_model_etf_missing_from_the_master_is_reported(master: dict[str, Path]) -> None:
    px = pd.read_parquet(master["prices_parquet"])
    px.loc[px["symbol"] != "VIXY"].to_parquet(master["prices_parquet"], index=False)
    result = gt.generate_targets(optimizer="inverse_vol")
    assert result["universe_missing"] == ["VIXY"] and result["symbols_considered"] == 20


def test_a_missing_latest_bar_keeps_the_etf_in_the_targets(master: dict[str, Path]) -> None:
    # VIXY's last bar didn't arrive (a failed fetch). Left out of the latest targets, the
    # basket would sell it whole as a holding outside the model and spread its weight
    # over the rest; it stays in at its last close instead.
    px = pd.read_parquet(master["prices_parquet"])
    last = px["date"].max()
    px.loc[~((px["symbol"] == "VIXY") & (px["date"] == last))].to_parquet(master["prices_parquet"], index=False)
    gt.generate_targets(optimizer="ensemble_default")
    targets = pd.read_parquet(master["targets_parquet"])

    latest = targets.loc[targets["rebalance_date"] == targets["rebalance_date"].max()]
    assert latest["rebalance_date"].iloc[0] == last
    assert set(latest["symbol"]) == set(gt.model_universe())
    vixy_close = px.loc[(px["symbol"] == "VIXY") & (px["date"] < last)].sort_values("date")["close"].iloc[-1]
    assert latest.set_index("symbol").loc["VIXY", "reference_price"] == pytest.approx(vixy_close)
    assert latest["target_weight"].sum() == pytest.approx(1.0)
    # one rebalance a month: no extra date holding VIXY alone at 100%
    assert targets.groupby(targets["rebalance_date"].dt.to_period("M"))["rebalance_date"].nunique().max() == 1

    # so the basket rebalances the VIXY it holds rather than selling all of it
    from src.production.runtime.build_paper_basket import build_reconciliation, load_latest_targets

    held = pd.DataFrame([{"symbol": "VIXY", "current_shares": 100, "sec_type": "STK", "market_price": vixy_close}])
    recon, _ = build_reconciliation(load_latest_targets(master["targets_parquet"]), held, 1_000_000.0)
    vixy = recon.set_index("symbol").loc["VIXY"]
    assert vixy["in_model"] and vixy["target_shares"] > 0


def test_an_etf_further_behind_than_the_staleness_limit_drops_out(master: dict[str, Path]) -> None:
    px = pd.read_parquet(master["prices_parquet"])
    sessions = sorted(px["date"].unique())
    cutoff = sessions[-(gt.MAX_LAG_SESSIONS + 2)]  # VIXY's last bar is 4 sessions old
    px.loc[~((px["symbol"] == "VIXY") & (px["date"] > cutoff))].to_parquet(master["prices_parquet"], index=False)
    gt.generate_targets(optimizer="inverse_vol")
    targets = pd.read_parquet(master["targets_parquet"])
    latest = targets.loc[targets["rebalance_date"] == targets["rebalance_date"].max()]
    assert "VIXY" not in set(latest["symbol"]) and len(latest) == 20
