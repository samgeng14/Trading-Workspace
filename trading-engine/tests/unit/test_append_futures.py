"""The futures appender asks IBKR for contracts that exist and keeps only
finished sessions. Until 2026-10-21 it passed the currency where ib_async's
ContFuture takes localSymbol, so five of the ten never qualified (BRN, ZC,
ZW, ZS, ZO; the grains were also on ECBOT and Brent on ICEEU)."""
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
    monkeypatch.setattr(afd, "MASTER_CSV", tmp_path / "futures_prices_master.csv")
    monkeypatch.setattr(afd, "MASTER_PARQUET", tmp_path / "futures_prices_master.parquet")
    monkeypatch.setattr(afd, "SNAPSHOTS_DIR", tmp_path / "snapshots")
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
