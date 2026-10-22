"""src/strategies/vix_premium.py is R3's rule as researched: the same Cboe file reading,
the same 30-day series, the same decisions and the same costs as
research/r3_vix_futures, which stays as it was run."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.walkforward")
from src.strategies import vix_premium as vp  # noqa: E402

HERE = PROJECT_ROOT / "research" / "r3_vix_futures"
HEADER = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest"


@pytest.fixture(scope="module")
def research():
    """(r3_vix_futures, its data module), loaded as the script loads them: its folder first
    on the path, so ``import data`` is R3's and not trading-engine/data."""
    saved = {k: sys.modules.pop(k) for k in ("data", "s1_trend") if k in sys.modules}
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location("r3_vix_futures", HERE / "r3_vix_futures.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = sys.modules["data"]
    finally:
        sys.path.remove(str(HERE))
        for k in ("data", "s1_trend"):
            sys.modules.pop(k, None)
        sys.modules.update(saved)
    return module, data


def expiries(start: date, n: int) -> list[pd.Timestamp]:
    """Monthly settlements on Wednesdays, as VX's rule sets them (about the middle of the month)."""
    out = []
    for k in range(n):
        y, m = start.year + (start.month - 1 + k) // 12, (start.month - 1 + k) % 12 + 1
        d = pd.Timestamp(y, m, 15)
        out.append(d + pd.Timedelta(days=(2 - d.weekday()) % 7))
    return out


def synthetic_table(seed: int = 5) -> tuple[pd.DataFrame, pd.Series]:
    """Settlements, trade date x expiry, upward-sloping with shocks, and a VIX that crosses
    the 30-day price both ways."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-02", "2025-12-31")
    exps = expiries(date(2025, 1, 1), 15)
    spot = 16 + np.cumsum(rng.normal(0, 0.6, len(days)))
    spot = np.clip(spot, 9, 45)
    table = pd.DataFrame(index=days, columns=exps, dtype=float)
    for e in exps:
        listed = e - pd.Timedelta(days=270)
        for i, t in enumerate(days):
            if listed <= t < e:
                months = (e - t).days / 30.0
                table.at[t, e] = round(spot[i] + 1.2 * np.sqrt(months) + rng.normal(0, 0.15), 4)
    table.iloc[40, 3] = np.nan  # a settlement that's missing
    vix = pd.Series(spot + rng.normal(0, 1.5, len(days)), index=days, name="vix")
    return table.dropna(axis=1, how="all"), vix


def test_the_30_day_series_is_the_researched_one(research) -> None:
    _, data = research
    table, _ = synthetic_table()
    ours, theirs = vp.cm30(table), data.cm30(table)
    pd.testing.assert_frame_equal(ours, theirs, check_exact=True)
    assert ours["er_return"].notna().sum() > 150


def test_the_decisions_are_the_researched_ones(research) -> None:
    module, data = research
    table, vix = synthetic_table()
    cm = data.cm30(table)
    ours, theirs = vp.rule(cm, vix), module.primary(cm, vix)
    shorts = flats = 0
    for t in cm.index[5:]:
        history = pd.DataFrame(index=cm.index[cm.index <= t])
        assert ours(history) == theirs(history), t
        shorts += vp.INSTRUMENT in ours(history)
        flats += vp.INSTRUMENT not in ours(history)
    assert shorts > 20 and flats > 20


def test_the_costs_and_the_walk_forward_are_the_researched_ones(research) -> None:
    from dv02_engine.walkforward import walk_forward

    module, data = research
    table, vix = synthetic_table()
    cm = data.cm30(table)
    days = cm.index[cm["er_return"].notna()]
    panel = pd.DataFrame({vp.INSTRUMENT: cm.loc[days, "er_level"], vp.CASH: 100 * 1.0002 ** np.arange(len(days))},
                         index=days)  # fmt: skip
    volumes = pd.DataFrame({vp.INSTRUMENT: np.nan, vp.CASH: 2e6}, index=days)
    runs = [walk_forward(panel, strategy, train=30, test=len(days) - 30, costs=c, n_trials=1, holdout=0,
                         rebalance_every=1, lag=1, volumes=volumes, mandate=True)
            for strategy, c in ((vp.rule(cm, vix), vp.costs()), (module.primary(cm, vix), module.COSTS))]  # fmt: skip
    pd.testing.assert_frame_equal(runs[0].equity, runs[1].equity, check_exact=True)
    assert runs[0].combined["total_cost"] > 0


def test_cboe_files_read_as_the_research_reads_them(research, tmp_path) -> None:
    _, data = research
    rows = ["2026-10-20,X (Nov 2026),18.6400,19.1500,18.2000,18.3000,18.4881,-0.1115,153388,808,223025",
            "2026-10-21,X (Nov 2026),18.2500,18.6500,18.1000,18.2400,18.2382,-0.2499,91982,0,218255",
            "2026-10-21,X (Nov 2026),18.2500,18.6500,18.1000,18.2400,18.2400,-0.2499,91982,0,218255",
            "2026-10-22,X (Nov 2026),0,0,0,0,0,0,0,0,0"]  # fmt: skip
    path = tmp_path / "VX_2026-11-18.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n")
    pd.testing.assert_frame_equal(vp.read_contract(path), data._read(path), check_exact=True)
    out = vp.contracts([path, tmp_path / "vx_list.json"])  # anything not a contract file is passed over
    assert out["settle"].iloc[:2].tolist() == [18.4881, 18.24]  # a date written twice: the last row
    assert np.isnan(out["settle"].iloc[2])  # Cboe's 0: no price
    assert out["expiry"].iloc[0] == pd.Timestamp("2026-11-18") and not out["final"].any()


def test_the_vix_file_reads_as_the_research_reads_it(research, tmp_path) -> None:
    _, data = research
    path = tmp_path / "VIX_History.csv"
    path.write_text("DATE,OPEN,HIGH,LOW,CLOSE\n10/20/2026,18.1,18.9,17.6,17.9\n10/21/2026,17.8,18.4,17.2,17.35\n")
    original = data.VIX_PATH
    data.VIX_PATH = path
    try:
        pd.testing.assert_series_equal(vp.read_vix(path), data.vix(), check_exact=True)
    finally:
        data.VIX_PATH = original
