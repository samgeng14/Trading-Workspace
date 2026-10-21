"""The evening futures curve archive (src/data/ingest/futures_curves.py): every
listed contract within the horizon, its bar for the session, and what IBKR
wouldn't serve, in its own words."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("duckdb")
from src.data import lake  # noqa: E402
from src.data.ingest import futures_curves as fc  # noqa: E402

SESSION = date(2026, 10, 21)
EVENING = datetime(2026, 10, 21, 21, 20, tzinfo=timezone.utc)  # 17:20 ET


class Event(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self

    def __isub__(self, handler):
        self.remove(handler)
        return self


def future(con_id: int, local: str, expiry: str, mult: str = "50"):
    contract = SimpleNamespace(conId=con_id, localSymbol=local, lastTradeDateOrContractMonth=expiry, multiplier=mult)
    return SimpleNamespace(contract=contract, realExpirationDate=expiry, contractMonth=expiry[:6])


def bar(day: date, close: float, volume: float = 1_000.0):
    return SimpleNamespace(date=day, open=close, high=close, low=close, close=close, volume=volume)


class FakeIB:
    """ES: two contracts in the horizon, one past it, one expired. CL: one with a
    negative close. VIX: one IBKR has no permission for. ZZ: nothing listed."""

    def __init__(self, stall: bool = False) -> None:
        self.errorEvent = Event()
        self.sleeps: list[float] = []
        self.stall = stall
        self.bar_requests = 0
        self.listed = {
            "ES": [future(1, "ESZ6", "20261218"), future(2, "ESH7", "20270319"), future(3, "ESZ7", "20271217"),
                   future(4, "ESU6", "20260918")],
            "CL": [future(5, "CLZ6", "20261119", "1000")],
            "VIX": [future(6, "VXX6", "20261118", "1000")],
            "ZZ": [],
        }  # fmt: skip

    def reqContractDetails(self, contract):  # noqa: N802
        return self.listed[contract.symbol]

    def reqHistoricalData(self, contract, **_kw):  # noqa: N802
        self.bar_requests += 1
        if self.stall:
            raise TimeoutError("reqHistoricalData timed out")
        if contract.conId == 6:
            for handler in list(self.errorEvent):
                handler(7, 162, "No market data permissions for CFE FUT", contract)
            return []
        close = -37.63 if contract.conId == 5 else 5_800.0 + contract.conId
        return [bar(date(2026, 10, 20), close - 10), bar(SESSION, close)]

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


ROOTS = [{"symbol": s, "exchange": "X", "currency": "USD"} for s in ("ES", "CL", "VIX", "ZZ")]


def test_every_contract_in_the_horizon_and_what_ibkr_wouldnt_serve() -> None:
    ib = FakeIB()
    rows, missing = fc.record_session(ib, ROOTS, SESSION, pace_s=0.0)
    assert list(rows["local_symbol"]) == ["ESZ6", "ESH7", "CLZ6"]  # not ESZ7 (past 400 days) or ESU6 (expired)
    assert list(rows["close"]) == [5_801.0, 5_802.0, -37.63]  # the session's bar, a negative one kept
    assert list(rows["days_to_expiry"]) == [58, 149, 29] and list(rows["multiplier"]) == [50.0, 50.0, 1_000.0]
    assert missing == ["VXX6: IBKR 162: No market data permissions for CFE FUT",
                       "ZZ@X: no contracts expiring within 400 days"]  # fmt: skip
    assert len(ib.sleeps) == 4 and ib.errorEvent == []  # paced, and its handler removed


def test_a_stalled_gateway_fails_fast() -> None:
    ib = FakeIB(stall=True)
    rows, missing = fc.record_session(ib, ROOTS[:1], SESSION, pace_s=0.0)
    assert rows.empty and ib.bar_requests == 1  # one timeout, then no more waiting
    assert missing[0] == "ESZ6: TimeoutError: reqHistoricalData timed out"
    assert missing[1].startswith("ESH7: TimeoutError: not asked: IBKR stopped answering")


def test_the_evening_run_writes_the_session_to_the_lake(tmp_path) -> None:
    client = SimpleNamespace(ib=FakeIB(), connect=lambda: None, disconnect=lambda: None)
    out = fc.archive(client=client, now=EVENING, root_dir=tmp_path, roots=ROOTS, pace_s=0.0)
    assert out["ok"] and out["session"] == "2026-10-21" and out["contracts"] == 3 and out["roots"] == 2
    kept = lake.read(fc.DATASET, keys=["con_id"], root=tmp_path)
    assert list(kept["local_symbol"]) == ["ESZ6", "ESH7", "CLZ6"]  # ordered by the key, con_id
    assert kept["available_at"].iloc[0] == EVENING


def test_nothing_on_a_day_the_market_is_closed(tmp_path) -> None:
    def refuse():
        raise AssertionError("no connection on a holiday")

    thanksgiving = datetime(2026, 11, 26, 22, 20, tzinfo=timezone.utc)
    out = fc.archive(client=SimpleNamespace(connect=refuse), now=thanksgiving, root_dir=tmp_path, roots=ROOTS)
    assert out == {"ok": True, "skipped": "2026-11-26 is not a session: nothing to record"}


def test_the_roots_are_the_configs_archive_list() -> None:
    roots = fc.archive_roots()
    assert {"symbol": "ES", "exchange": "CME", "currency": "USD"} in roots and len(roots) == 23
