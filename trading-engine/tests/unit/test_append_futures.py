"""The futures and indices appenders ask IBKR for contracts that exist and keep
only finished sessions. Until 2026-10-21 the futures one passed the currency
where ib_async's ContFuture takes localSymbol, so five of the ten never
qualified (BRN, ZC, ZW, ZS, ZO; the grains were also on ECBOT and Brent on
ICEEU), and both kept the partial bar of a session still trading: the VM's
indices master had VIX's pre-market bar for 2026-10-21."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production.pipeline import append_futures_daily as afd  # noqa: E402
from src.production.pipeline import append_indices_daily as aid  # noqa: E402


class FakeIB:
    """Qualifies anything and returns a bar for each day in ``days``."""

    def __init__(self, days: list[str]) -> None:
        self.days = days
        self.contracts: list = []

    def qualifyContracts(self, contract):  # noqa: N802 (ib_async's name)
        self.contracts.append(contract)
        return [contract]

    def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
        return [SimpleNamespace(date=d, open=1.0, high=2.0, low=0.5, close=1.5, volume=10) for d in self.days]

    def disconnect(self) -> None:
        pass


@pytest.fixture
def box(tmp_path: Path, monkeypatch):
    for mod, name in ((afd, "futures"), (aid, "indices")):
        monkeypatch.setattr(mod, "MASTER_CSV", tmp_path / f"{name}_prices_master.csv")
        monkeypatch.setattr(mod, "MASTER_PARQUET", tmp_path / f"{name}_prices_master.parquet")
        monkeypatch.setattr(mod, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    ib = FakeIB(["2026-10-19", "2026-10-20", "2026-10-21"])
    from src.broker.ibkr import client

    monkeypatch.setattr(client, "connect_ib", lambda *a, **k: ib)
    return ib


def test_contracts_carry_their_currency_by_name(box) -> None:
    out = afd.append_futures_daily(through=date(2026, 10, 20))
    assert out["ok"] and out["errors"] == []
    got = {c.symbol: c for c in box.contracts}
    assert set(got) == {spec[0] for spec in afd.FUTURES_CONTRACTS.values()}
    assert all(c.currency == "USD" and c.localSymbol == "" for c in got.values())
    assert (got["COIL"].exchange, got["ZC"].exchange, got["RTY"].exchange) == ("IPE", "CBOT", "CME")


def test_an_unfinished_session_is_left_out(box) -> None:
    out = afd.append_futures_daily(through=date(2026, 10, 20))
    master = pd.read_csv(afd.MASTER_CSV)
    assert master["date"].max() == "2026-10-20"
    assert out["new_rows_added_to_master"] == 2 * len(afd.FUTURES_CONTRACTS)
    assert set(master.loc[master["symbol"] == "BRN", "date"]) == {"2026-10-19", "2026-10-20"}


def test_an_unfinished_index_bar_is_left_out_and_an_old_one_dropped(box) -> None:
    # what the VM's master held: VIX's pre-market bar for 2026-10-21
    pd.DataFrame([{"date": "2026-10-21", "symbol": "VIX", "open": 17.25, "high": 17.25, "low": 17.13,
                   "close": 17.18, "volume": 0.0}]).to_parquet(aid.MASTER_PARQUET, index=False)  # fmt: skip
    out = aid.append_indices_daily(through=date(2026, 10, 20))
    master = pd.read_csv(aid.MASTER_CSV)
    assert out["ok"] and out["latest_date"] == "2026-10-20" and out["new_rows_added_to_master"] == 4
    assert sorted(master.loc[master["symbol"] == "VIX", "date"]) == ["2026-10-19", "2026-10-20"]
    assert {c.symbol: c.currency for c in box.contracts} == {"VIX": "USD", "DX": "USD"}
