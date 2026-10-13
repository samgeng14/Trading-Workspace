"""The price master stays on one price basis.

Reproduces the ETHA corruption on the owner's Mac: the master held ETHA on the
old basis (2026-02-13 close 15.44); after a 1-for-3 reverse split IBKR's
split-adjusted history is 3x that (46.32), and splicing IBKR's bars onto the
old rows left a fake +220% day on 2026-02-13. Every IBKR fetch here is a fake.
"""
from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd
import pytest

from src.data.cleaning import price_basis as pb
from src.data.cleaning.validators import validate_master_prices
from src.production.pipeline import append_ibkr_daily as app

TODAY = pd.Timestamp("2026-10-12")
MASTER_END = pd.Timestamp("2026-05-06")      # the committed master's last day
IBKR_END = pd.Timestamp("2026-10-09")        # last full session before TODAY


# ── Fixtures and fakes ───────────────────────────────────────────────────────

def _series(symbol: str, start: str, end: str, level: float, seed: int, pins: dict | None = None) -> pd.DataFrame:
    """Deterministic daily bars: a gentle random walk with optional pinned closes."""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    close = level * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
    df = pd.DataFrame({"date": dates, "symbol": symbol, "close": close})
    for d, v in (pins or {}).items():
        df.loc[df["date"] == pd.Timestamp(d), "close"] = v
    df["open"] = df["close"] * 0.995
    df["high"] = df["close"] * 1.01
    df["low"] = df["close"] * 0.99
    df["volume"] = 1_000_000.0
    return df[pb.COLUMNS]


def _scaled(df: pd.DataFrame, factor: float) -> pd.DataFrame:
    out = df.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] * factor
    return out


# ETHA on the committed master's (old) basis: the real closes around the seam.
ETHA_PINS = {"2026-02-12": 14.46, "2026-02-13": 15.44, "2026-02-17": 15.05}


def etha_old() -> pd.DataFrame:
    return _series("ETHA", "2025-09-01", IBKR_END, 15.0, seed=1, pins=ETHA_PINS)


def spy() -> pd.DataFrame:
    return _series("SPY", "2025-09-01", IBKR_END, 600.0, seed=2)


def master_through(df: pd.DataFrame, end: pd.Timestamp = MASTER_END) -> pd.DataFrame:
    return df[df["date"] <= end].reset_index(drop=True)


class FakeIBKR:
    """``fetch(symbol, duration)`` over fixed per-symbol IBKR histories.

    Durations of 10 years or more are full-history refetches; anything
    shorter returns the bars from TODAY minus the duration onwards.
    """

    def __init__(self, histories: dict[str, pd.DataFrame], *, fail_full: dict[str, Exception | None] | None = None):
        self.histories = histories
        self.fail_full = fail_full or {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, symbol: str, duration: str) -> pd.DataFrame:
        self.calls.append((symbol, duration))
        hist = self.histories[symbol]
        n, unit = duration.split()
        if unit == "Y" and int(n) >= 10:
            if symbol in self.fail_full:
                exc = self.fail_full[symbol]
                if exc is None:
                    return pd.DataFrame()
                raise exc
            return hist.copy()
        days = int(n) * {"D": 1, "W": 7, "M": 31, "Y": 365}[unit]
        return hist[hist["date"] >= TODAY - pd.Timedelta(days=days)].copy()

    def full_requests(self, symbol: str) -> list[str]:
        return [d for s, d in self.calls if s == symbol and d.endswith("Y") and int(d.split()[0]) >= 10]


@pytest.fixture()
def disk(tmp_path, monkeypatch):
    """Point the append module's master and snapshot paths at a temp dir."""
    monkeypatch.setattr(app, "MASTER_CSV", tmp_path / "etf_prices_master.csv")
    monkeypatch.setattr(app, "MASTER_PARQUET", tmp_path / "etf_prices_master.parquet")
    monkeypatch.setattr(app, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    def write(df: pd.DataFrame) -> None:
        df.to_parquet(app.MASTER_PARQUET, index=False)
        df.to_csv(app.MASTER_CSV, index=False)

    def read() -> pd.DataFrame:
        return pd.read_parquet(app.MASTER_PARQUET)

    return type("Disk", (), {"write": staticmethod(write), "read": staticmethod(read), "dir": tmp_path})


def _close(df: pd.DataFrame, symbol: str, day: str) -> float:
    return float(df.loc[(df["symbol"] == symbol) & (pd.to_datetime(df["date"]) == pd.Timestamp(day)), "close"].iloc[0])


# ── 1. The ETHA case, merge path ─────────────────────────────────────────────

def test_naive_splice_reproduces_the_macs_fake_220pct_day() -> None:
    """What the old merge (concat + keep='last') did with the same IBKR bars."""
    master = pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True)
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()})
    fetched = ibkr("ETHA", "241 D")                       # back to 2026-02-13
    spliced = pd.concat([master, fetched]).drop_duplicates(["symbol", "date"], keep="last")
    jumps = pb.find_bad_jumps(spliced)
    assert [(j.symbol, str(j.date.date()), j.prev_close, round(j.close, 2)) for j in jumps] == [
        ("ETHA", "2026-02-13", 14.46, 46.32)
    ]
    assert jumps[0].ratio - 1 == pytest.approx(2.2033195, rel=1e-6)   # the validator's pct_change


def test_etha_merge_refetches_the_whole_history_and_leaves_no_jump(disk) -> None:
    disk.write(pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True))
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()})

    result = app.append_bars(ibkr, lookback="241 D", today=TODAY)

    assert result["ok"] is True
    assert result["refetched"] == ["ETHA"]
    assert result["refused"] == []
    assert result["basis"]["ETHA"]["status"] == "refetched"
    assert result["basis"]["ETHA"]["median_ratio"] == pytest.approx(3.0)
    assert result["basis"]["SPY"]["status"] == "merged"
    assert ibkr.full_requests("ETHA") == [app.FULL_HISTORY_DURATIONS[0]]
    assert ibkr.full_requests("SPY") == []

    out = disk.read()
    assert _close(out, "ETHA", "2026-02-13") == pytest.approx(46.32)
    assert _close(out, "ETHA", "2026-02-12") == pytest.approx(14.46 * 3)
    assert pb.find_bad_jumps(out) == []
    assert out[out["symbol"] == "ETHA"]["date"].max() == IBKR_END
    assert out[out["symbol"] == "SPY"]["date"].max() == IBKR_END
    assert validate_master_prices(out).errors == []
    # the master as it was before the history was replaced is kept
    assert result["backup_path"] and (disk.dir / "snapshots").exists()
    assert len(pd.read_parquet(result["backup_path"])) == len(
        pd.concat([master_through(etha_old()), master_through(spy())])
    )


def test_daily_append_stretches_its_lookback_to_overlap_the_master(disk) -> None:
    """The runner's 5 D fetch after a 5-month gap still overlaps the stored rows."""
    disk.write(pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True))
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()})

    result = app.append_bars(ibkr, lookback="5 D", today=TODAY)

    assert result["lookbacks"] == {"ETHA": "169 D", "SPY": "169 D"}   # 159 days behind + 10
    assert result["basis"]["ETHA"]["overlap_days"] >= 5
    assert result["refetched"] == ["ETHA"]
    assert pb.find_bad_jumps(disk.read()) == []


def test_lookback_for() -> None:
    assert app.lookback_for(pd.Timestamp("2026-10-09"), "5 D", TODAY) == "13 D"
    assert app.lookback_for(pd.Timestamp("2026-10-09"), "45 D", TODAY) == "45 D"
    assert app.lookback_for(pd.Timestamp("2025-01-02"), "5 D", TODAY) == "2 Y"
    assert app.lookback_for(None, "5 D", TODAY) == "5 D"


# ── 2. Dividend-sized drift is not a basis change ────────────────────────────

def test_dividend_sized_drift_merges_without_a_refetch(disk) -> None:
    # IBKR's whole back history 0.3% lower, as a dividend adjustment does.
    old = etha_old()
    disk.write(master_through(old))
    ibkr = FakeIBKR({"ETHA": _scaled(old, 0.997)})

    result = app.append_bars(ibkr, lookback="5 D", today=TODAY)

    assert result["ok"] is True
    assert result["refetched"] == []
    assert result["basis"]["ETHA"]["status"] == "merged"
    assert result["basis"]["ETHA"]["max_deviation"] == pytest.approx(0.003, abs=1e-9)
    assert ibkr.full_requests("ETHA") == []
    assert disk.read()["date"].max() == IBKR_END


def test_a_provisional_last_row_does_not_trigger_a_refetch(disk) -> None:
    # The master's newest row was written intraday, 2% off the final close.
    old = etha_old()
    stored = master_through(old).copy()
    stored.loc[stored.index[-1], "close"] *= 1.02
    disk.write(stored)
    ibkr = FakeIBKR({"ETHA": old})

    result = app.append_bars(ibkr, lookback="5 D", today=TODAY)

    assert result["basis"]["ETHA"]["status"] == "merged"
    assert ibkr.full_requests("ETHA") == []
    assert _close(disk.read(), "ETHA", str(MASTER_END.date())) == pytest.approx(_close(old, "ETHA", str(MASTER_END.date())))


# ── 3. A failed refetch refuses the merge, loudly ───────────────────────────

def test_refetch_failure_refuses_the_merge_and_reports(disk, caplog) -> None:
    stored = pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True)
    disk.write(stored)
    ibkr = FakeIBKR(
        {"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()},
        fail_full={"ETHA": ConnectionError("pacing violation")},
    )

    with caplog.at_level(logging.ERROR):
        result = app.append_bars(ibkr, lookback="5 D", today=TODAY)

    assert result["ok"] is False
    assert [r["symbol"] for r in result["refused"]] == ["ETHA"]
    assert "pacing violation" in result["refused"][0]["reason"]
    assert "bin/tradeit refresh" in result["error"] and "ETHA" in result["error"]
    assert any("REFUSED" in r.getMessage() and "ETHA" in r.getMessage() for r in caplog.records)
    assert ibkr.full_requests("ETHA") == list(app.FULL_HISTORY_DURATIONS)   # every duration tried

    out = disk.read()
    etha = out[out["symbol"] == "ETHA"].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        etha[["date", "close"]], master_through(etha_old())[["date", "close"]], check_dtype=False
    )
    assert out[out["symbol"] == "SPY"]["date"].max() == IBKR_END        # others still merged
    assert pb.find_bad_jumps(out) == []


def test_a_refetched_history_with_its_own_jump_is_refused(disk) -> None:
    """IBKR returning a not-yet-adjusted history must not replace the master either."""
    old = etha_old()
    unadjusted = pd.concat([old[old["date"] < "2026-06-01"], _scaled(old[old["date"] >= "2026-06-01"], 3.0)])
    disk.write(master_through(old))
    ibkr = FakeIBKR({"ETHA": _scaled(old, 3.0)})
    ibkr.histories["ETHA_FULL"] = unadjusted

    def fetch(symbol: str, duration: str) -> pd.DataFrame:
        if duration.endswith("Y") and int(duration.split()[0]) >= 10:
            return unadjusted.copy()
        return ibkr(symbol, duration)

    result = app.append_bars(fetch, lookback="5 D", today=TODAY)
    assert result["ok"] is False
    assert "bad jumps of its own" in result["refused"][0]["reason"]
    assert disk.read()["date"].max() == MASTER_END


def test_split_day_bars_ibkr_has_not_rescaled_yet_are_refused(disk) -> None:
    """IBKR sends the post-split bar before rescaling the older ones: the overlap
    agrees, but merging would write a x3 jump into the master. Refused instead;
    the next run, once IBKR has rescaled, sees the mismatch and refetches."""
    old = etha_old()
    not_rescaled = old.copy()
    not_rescaled.loc[not_rescaled["date"] >= "2026-10-08", ["open", "high", "low", "close"]] *= 3
    disk.write(old[old["date"] <= "2026-10-02"])
    ibkr = FakeIBKR({"ETHA": not_rescaled})

    result = app.append_bars(ibkr, lookback="5 D", today=TODAY)

    assert result["ok"] is False
    reason = result["refused"][0]["reason"]
    assert "ETHA 2026-10-08" in reason and "bad jumps of its own" in reason
    assert disk.read()["date"].max() == pd.Timestamp("2026-10-02")
    assert pb.find_bad_jumps(disk.read()) == []

    # Next day IBKR has rescaled its whole history: the merge refetches and is clean.
    ibkr.histories["ETHA"] = _scaled(old, 3.0)
    result = app.append_bars(ibkr, lookback="5 D", today=TODAY)
    assert result["ok"] is True and result["refetched"] == ["ETHA"]
    assert pb.find_bad_jumps(disk.read()) == []


def test_full_history_falls_back_to_shorter_durations() -> None:
    calls: list[str] = []

    def fetch(symbol: str, duration: str) -> pd.DataFrame:
        calls.append(duration)
        if duration == app.FULL_HISTORY_DURATIONS[0]:
            raise RuntimeError("invalid duration")
        return spy()

    assert len(app.fetch_full_history(fetch, "SPY")) == len(spy())
    assert calls == list(app.FULL_HISTORY_DURATIONS[:2])


# ── 4. Repairing a master that is already mixed ─────────────────────────────

def mac_master() -> pd.DataFrame:
    """The owner's master after the refresh: ETHA x3 from 2026-02-13 on."""
    etha = etha_old()
    mixed = pd.concat([etha[etha["date"] < "2026-02-13"], _scaled(etha[etha["date"] >= "2026-02-13"], 3.0)])
    return pd.concat([mixed, spy()], ignore_index=True)


def test_repair_finds_and_fixes_the_mixed_master(disk) -> None:
    disk.write(mac_master())
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()})

    result = app.repair_bars(ibkr)

    assert result["ok"] is True
    assert result["refetched"] == ["ETHA"]
    assert "2026-02-13" in result["basis"]["ETHA"]["reason"]
    assert result["basis"]["SPY"]["status"] == "unchanged"
    assert ibkr.full_requests("SPY") == []
    out = disk.read()
    assert pb.find_bad_jumps(out) == []
    assert _close(out, "ETHA", "2026-02-12") == pytest.approx(14.46 * 3)
    assert validate_master_prices(out).errors == []
    assert result["backup_path"]


def test_repair_catches_a_whole_history_on_an_old_basis(disk) -> None:
    """No jump inside the stored rows, but IBKR's bars are now 3x: still repaired."""
    disk.write(etha_old())
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0)})

    result = app.repair_bars(ibkr, symbols=["etha"])

    assert result["refetched"] == ["ETHA"]
    assert result["basis"]["ETHA"]["median_ratio"] == pytest.approx(3.0)
    assert _close(disk.read(), "ETHA", "2026-02-13") == pytest.approx(46.32)


def test_repair_leaves_a_clean_master_alone(disk) -> None:
    clean = pd.concat([etha_old(), spy()], ignore_index=True)
    disk.write(clean)
    before = disk.read()
    ibkr = FakeIBKR({"ETHA": etha_old(), "SPY": spy()})

    result = app.repair_bars(ibkr)

    assert result["ok"] is True and result["refetched"] == [] and result["backup_path"] is None
    assert ibkr.full_requests("ETHA") == ibkr.full_requests("SPY") == []
    pd.testing.assert_frame_equal(disk.read(), before)


def test_repair_that_cannot_refetch_fails_and_keeps_the_master(disk) -> None:
    disk.write(mac_master())
    before = disk.read()
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()}, fail_full={"ETHA": None})

    result = app.repair_bars(ibkr, symbols=["ETHA"])

    assert result["ok"] is False
    assert result["refused"][0]["symbol"] == "ETHA"
    assert "--repair-basis --symbols ETHA" in result["error"]
    pd.testing.assert_frame_equal(disk.read(), before)


def test_backfill_cli_repair_flag(monkeypatch) -> None:
    from src.production import backfill_gaps

    seen: dict = {}

    def fake_repair(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "symbols_checked": ["ETHA"], "refetched": [], "refused": [], "unchecked": []}

    monkeypatch.setattr(backfill_gaps, "repair_ibkr_basis", fake_repair)
    monkeypatch.setattr(sys, "argv", ["backfill_gaps", "--repair-basis", "--symbols", "ETHA", "--port", "4002"])
    with pytest.raises(SystemExit) as exit_:
        backfill_gaps.main()
    assert exit_.value.code == 0
    assert seen["symbols"] == ["ETHA"] and seen["window"] == "1 Y" and seen["port"] == 4002

    monkeypatch.setattr(
        backfill_gaps, "repair_ibkr_basis",
        lambda **kw: {"ok": False, "symbols_checked": ["ETHA"], "refetched": [], "unchecked": [],
                      "refused": [{"symbol": "ETHA", "reason": "x"}], "error": "refused"},
    )
    with pytest.raises(SystemExit) as exit_:
        backfill_gaps.main()
    assert exit_.value.code == 1


# ── 5. The validator blocks bad data, warns on real moves ───────────────────

def test_validator_blocks_the_split_ratio_jump_with_what_to_run() -> None:
    v = validate_master_prices(mac_master())
    assert v.ok is False
    assert len(v.errors) == 1
    msg = v.errors[0]
    assert "ETHA 2026-02-13" in msg and "14.46 -> 46.32" in msg and "bin/tradeit refresh" in msg
    assert not any("suspicious" in w for w in v.warnings)   # reported once, as the error


def test_validator_blocks_a_split_below_the_50pct_warning_line() -> None:
    # A 2-for-1 split on a +5% day: -47.5%, under the old >50% warning threshold.
    df = spy()
    df.loc[df["date"] >= "2026-08-03", "close"] *= 0.5 * 1.05
    v = validate_master_prices(df)
    assert v.ok is False
    assert "SPY 2026-08-03" in v.errors[0] and "2-for-1 split" in v.errors[0]


def test_validator_blocks_a_one_day_spike_that_reverts() -> None:
    df = spy()
    df.loc[df["date"] == "2026-08-03", "close"] *= 1.8
    v = validate_master_prices(df)
    assert v.ok is False
    assert len(v.errors) == 1
    assert "SPY 2026-08-03" in v.errors[0] and "spike" in v.errors[0] and "bin/tradeit refresh" in v.errors[0]


def test_validator_blocks_a_split_ratio_spike_once() -> None:
    df = spy()
    df.loc[df["date"] == "2026-08-03", "close"] *= 3.0
    v = validate_master_prices(df)
    assert v.ok is False and len(v.errors) == 1 and "spike" in v.errors[0]


def test_validator_only_warns_on_a_large_move_that_persists() -> None:
    df = spy()
    df.loc[df["date"] >= "2026-08-03", "close"] *= 1.6     # +60%, stays, not a split ratio
    v = validate_master_prices(df)
    assert v.ok is True
    assert v.errors == []
    assert any("suspicious" in w and "SPY" in w for w in v.warnings)


def test_jumps_older_than_the_signal_window_do_not_block() -> None:
    df = _series("SPY", "2023-01-02", IBKR_END, 400.0, seed=3)
    df.loc[df["date"] < "2024-01-02", "close"] /= 3.0     # ~2.8 years before the last day
    assert pb.find_bad_jumps(df)                           # still visible to the repair
    assert validate_master_prices(df).errors == []


# ── 6. The pre-trade gate blocks on it ──────────────────────────────────────

def _basket() -> pd.DataFrame:
    return pd.DataFrame([
        {"symbol": "SPY", "target_weight": 0.30, "estimated_trade_dollars": 30_000, "side": "buy"},
        {"symbol": "ETHA", "target_weight": 0.10, "estimated_trade_dollars": 10_000, "side": "buy"},
    ])


def test_pre_trade_gate_blocks_on_a_mixed_master(tmp_path) -> None:
    from src.runtime.risk_checks import run_pre_trade_risk_checks

    path = tmp_path / "etf_prices_master.parquet"
    mac_master().to_parquet(path, index=False)
    res = run_pre_trade_risk_checks(basket_df=_basket(), nav=100_000.0, prices_path=path)
    assert res.ok is False
    hits = [r for r in res.blocking_reasons if r.startswith("prices:") and "ETHA 2026-02-13" in r]
    assert hits and "bin/tradeit refresh" in hits[0]
    assert "BLOCK" in res.report()


def test_pre_trade_gate_fails_closed_when_validation_crashes(tmp_path, monkeypatch) -> None:
    import src.data.cleaning.validators as validators
    from src.runtime.risk_checks import run_pre_trade_risk_checks

    def boom(path):
        raise OSError("disk gone")

    monkeypatch.setattr(validators, "validate_master_prices_file", boom)
    res = run_pre_trade_risk_checks(basket_df=_basket(), nav=100_000.0, prices_path=tmp_path / "x.parquet")
    assert res.ok is False
    assert any("validation could not run" in r for r in res.blocking_reasons)


# ── Unit pieces ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("ratio", "factor"),
    [(3.2033, 3.0), (2.0, 2.0), (0.525, 0.5), (1 / 3, 1 / 3), (10.4, 10.0), (1.43, None), (0.655, None), (1.6, None)],
)
def test_split_factor(ratio: float, factor: float | None) -> None:
    got = pb.split_factor(ratio)
    assert (got is None and factor is None) or got == pytest.approx(factor)


def test_check_overlap_without_shared_dates_is_not_consistent() -> None:
    a = master_through(spy(), pd.Timestamp("2026-03-01"))
    b = spy()[spy()["date"] > "2026-06-01"]
    ov = pb.check_overlap(a, b)
    assert ov.days == 0 and ov.consistent is False


def test_no_overlap_is_treated_as_a_basis_mismatch() -> None:
    a = master_through(spy(), pd.Timestamp("2026-03-01"))
    b = spy()[spy()["date"] > "2026-06-01"]
    res = pb.merge_symbol_bars("SPY", a, b, refetch=lambda s: spy())
    assert res.status == "refetched"
    assert pb.find_bad_jumps(res.rows) == []
    res = pb.merge_symbol_bars("SPY", a, b, refetch=None)
    assert res.status == "refused"


# ── Through the IBKR plumbing, with a fake IB ───────────────────────────────

class FakeIB:
    """Just enough of ib_async.IB for _fetch_symbol_bars, over FakeIBKR histories."""

    def __init__(self, ibkr: FakeIBKR):
        self.ibkr = ibkr
        self.connected = True
        self.requests: list[tuple[str, str, str]] = []

    def qualifyContracts(self, contract):  # noqa: N802
        return [contract]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate):  # noqa: N802,N803
        assert self.connected, "historical data requested after disconnect"
        self.requests.append((contract.symbol, durationStr, whatToShow))
        bars = self.ibkr(contract.symbol, durationStr)
        return [
            type("Bar", (), {"date": r.date.date(), "open": r.open, "high": r.high, "low": r.low,
                             "close": r.close, "volume": r.volume})()
            for r in bars.itertuples(index=False)
        ]

    def disconnect(self):
        self.connected = False


def test_append_ibkr_daily_refetches_over_the_open_connection(disk, monkeypatch) -> None:
    disk.write(pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True))
    fake = FakeIB(FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()}))
    monkeypatch.setattr(app, "_connect", lambda host, port, client_id: fake)

    result = app.append_ibkr_daily(profile="paper", lookback="5 D")

    assert result["ok"] is True and result["refetched"] == ["ETHA"]
    assert ("ETHA", app.FULL_HISTORY_DURATIONS[0], app.BAR_TYPE) in fake.requests
    assert {w for _, _, w in fake.requests} == {app.BAR_TYPE}     # one bar type for every request
    assert fake.connected is False
    assert pb.find_bad_jumps(disk.read()) == []


def test_repair_ibkr_basis_over_a_fake_ib(disk, monkeypatch) -> None:
    disk.write(mac_master())
    fake = FakeIB(FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()}))
    monkeypatch.setattr(app, "_connect", lambda host, port, client_id: fake)

    result = app.repair_ibkr_basis(symbols=["ETHA"])

    assert result["ok"] is True and result["refetched"] == ["ETHA"]
    assert [d for s, d, _ in fake.requests] == ["1 Y", app.FULL_HISTORY_DURATIONS[0]]
    assert fake.connected is False
    assert pb.find_bad_jumps(disk.read()) == []


def test_connection_failure_is_reported_not_raised(disk, monkeypatch) -> None:
    disk.write(spy())

    def refuse(host, port, client_id):
        raise ConnectionRefusedError("nothing on 4002")

    monkeypatch.setattr(app, "_connect", refuse)
    for fn in (app.append_ibkr_daily, app.repair_ibkr_basis):
        result = fn(port=4002)
        assert result["ok"] is False and "nothing on 4002" in result["error"]


# ── Review: gaps the checks above leave open ─────────────────────────────────

def test_every_ratio_beyond_the_smallest_split_matches() -> None:
    """No gaps between factors: 5-for-2, 1-for-6, 1-for-15 ... all match."""
    for r in np.geomspace(1.7392, 5000, 20000):
        assert pb.split_factor(r) is not None and pb.split_factor(1 / r) is not None, r
    for r in np.geomspace(1 / 1.7390, 1.7390, 2000):
        assert pb.split_factor(r) is None, r
    assert pb.describe_factor(0.4) == "/2.5 (a 5-for-2 split)"
    assert pb.describe_factor(3.0) == "x3 (a 1-for-3 reverse split)"


@pytest.mark.parametrize("ratio", [2.4, 0.41, 0.4, 6.0, 1 / 6, 12.0, 15.0, 1 / 15, 40.0])
def test_validator_blocks_splits_with_uncommon_ratios(ratio: float) -> None:
    # Used to draw only the ">50%" warning: the ratio fell between the listed factors.
    df = spy()
    df.loc[df["date"] >= "2026-08-03", "close"] *= ratio
    v = validate_master_prices(df)
    assert v.ok is False
    assert len(v.errors) == 1 and "SPY 2026-08-03" in v.errors[0] and "split" in v.errors[0]


@pytest.mark.parametrize("factor", [2 / 3, 1.01])
def test_a_basis_change_with_no_split_ratio_jump_is_caught_by_the_overlap_alone(disk, factor: float) -> None:
    """A 3-for-2 split (x2/3) or a 1% shift: the seam check can't see either,
    so only the overlap comparison stands between them and a splice."""
    old = etha_old()
    disk.write(master_through(old))
    ibkr = FakeIBKR({"ETHA": _scaled(old, factor)})

    result = app.append_bars(ibkr, lookback="5 D", today=TODAY)

    assert result["ok"] is True and result["refetched"] == ["ETHA"]
    out = disk.read()
    expected = _scaled(old, factor)
    assert len(out) == len(expected)
    np.testing.assert_allclose(out["close"].to_numpy(), expected["close"].to_numpy())


def test_a_refetch_that_disagrees_with_the_bars_just_fetched_is_refused(disk) -> None:
    # The daily bars are on the new (x3) basis, the long request still on the old one.
    old = etha_old()
    disk.write(master_through(old))
    ibkr = FakeIBKR({"ETHA": _scaled(old, 3.0)})

    def fetch(symbol: str, duration: str) -> pd.DataFrame:
        if duration in app.FULL_HISTORY_DURATIONS:
            return old.copy()
        return ibkr(symbol, duration)

    result = app.append_bars(fetch, lookback="5 D", today=TODAY)

    assert result["ok"] is False
    assert "disagrees with the bars just fetched" in result["refused"][0]["reason"]
    assert disk.read()["date"].max() == MASTER_END


def test_todays_bar_moving_between_the_fetch_and_the_refetch_is_not_a_disagreement() -> None:
    # A run during market hours: today's bar is still trading and moves 0.8%
    # between the daily request and the full-history request seconds later.
    old = etha_old()
    new = _scaled(old, 3.0)
    new = new[new["date"] >= "2026-04-26"]
    later = _scaled(old, 3.0)
    later.loc[later.index[-1], "close"] *= 1.008

    res = pb.merge_symbol_bars("ETHA", master_through(old), new, refetch=lambda s: later)

    assert res.status == "refetched"
    assert pb.find_bad_jumps(res.rows) == []


def _short_full_history(ibkr: FakeIBKR, bars: int):
    def fetch(symbol: str, duration: str) -> pd.DataFrame:
        if duration in app.FULL_HISTORY_DURATIONS:
            return ibkr.histories[symbol].tail(bars).copy()
        return ibkr(symbol, duration)
    return fetch


def test_a_short_refetch_is_refused_instead_of_wiping_the_history(disk) -> None:
    stored = pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True)
    disk.write(stored)
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()})

    result = app.append_bars(_short_full_history(ibkr, 30), lookback="5 D", today=TODAY)

    assert result["ok"] is False
    assert "returned only 30 bars" in result["refused"][0]["reason"]
    etha = disk.read().query("symbol == 'ETHA'").reset_index(drop=True)
    pd.testing.assert_frame_equal(
        etha[["date", "close"]], master_through(etha_old())[["date", "close"]], check_dtype=False
    )


def test_a_short_refetch_is_refused_by_the_repair_too(disk) -> None:
    disk.write(mac_master())
    before = disk.read()
    ibkr = FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()})

    result = app.repair_bars(_short_full_history(ibkr, 30), symbols=["ETHA"])

    assert result["ok"] is False and "returned only 30 bars" in result["refused"][0]["reason"]
    pd.testing.assert_frame_equal(disk.read(), before)


def test_a_long_enough_refetch_may_start_later_than_the_stored_history() -> None:
    # IBKR's history can be shorter than an old source's (SPY from 1993): fine
    # as long as it covers the window the signals read.
    stored = _series("SPY", "2018-01-02", "2026-05-06", 400.0, seed=4)
    ibkr = _scaled(_series("SPY", "2018-01-02", IBKR_END, 400.0, seed=4), 0.5)
    new = ibkr[ibkr["date"] >= "2026-04-26"]
    res = pb.merge_symbol_bars("SPY", stored, new, refetch=lambda s: ibkr[ibkr["date"] >= "2024-01-02"])
    assert res.status == "refetched" and res.first_date == "2024-01-02" and res.rows_dropped > 0


def test_a_refetch_that_stops_before_the_new_bars_is_refused() -> None:
    old = etha_old()
    new = _scaled(old, 3.0)
    new = new[new["date"] >= "2026-04-26"]
    stops_early = _scaled(old, 3.0)
    stops_early = stops_early[stops_early["date"] < "2026-01-01"]

    res = pb.merge_symbol_bars("ETHA", master_through(old), new, refetch=lambda s: stops_early)

    assert res.status == "refused" and "ends 2025-12-31" in res.reason
    # and without stored rows after it, still refused: it must overlap the new bars
    res = pb.replace_history("ETHA", master_through(old, pd.Timestamp("2025-12-31")), new,
                             lambda s: stops_early, "test")
    assert res.status == "refused" and "doesn't overlap the bars just fetched" in res.reason


def test_pre_trade_gate_blocks_on_validator_errors_even_if_ok_says_pass(tmp_path, monkeypatch) -> None:
    import src.data.cleaning.validators as validators
    from src.runtime.risk_checks import run_pre_trade_risk_checks

    monkeypatch.setattr(
        validators, "validate_master_prices_file",
        lambda path: validators.PriceValidationResult(ok=True, errors=["ETHA 2026-02-13: bad"]),
    )
    res = run_pre_trade_risk_checks(basket_df=_basket(), nav=100_000.0, prices_path=tmp_path / "x.parquet")
    assert res.ok is False and "prices: ETHA 2026-02-13: bad" in res.blocking_reasons


def test_runner_stops_before_targets_when_a_merge_is_refused(disk, monkeypatch) -> None:
    """The 16:32 path end to end (fake IB): a refused merge aborts the run
    before targets, baskets and orders, and the run log names the symbol."""
    from src.production import daily_runner

    disk.write(pd.concat([master_through(etha_old()), master_through(spy())], ignore_index=True))
    fake = FakeIB(FakeIBKR({"ETHA": _scaled(etha_old(), 3.0), "SPY": spy()},
                           fail_full={"ETHA": ConnectionError("pacing violation")}))
    monkeypatch.setattr(app, "_connect", lambda host, port, client_id: fake)
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", disk.dir)
    called: list[str] = []
    # Every later step is a stub, so even a regression can't reach a real gateway or real files.
    for step in ("step_append_indices_daily", "step_append_futures_daily", "step_generate_targets",
                 "step_build_basket", "step_submit_orders"):
        monkeypatch.setattr(daily_runner, step, lambda _s=step, **kw: called.append(_s) or {"ok": False})

    result = daily_runner.run(dry_run=True)

    assert called == []
    assert result["ok"] is False and result["error"] == "append_daily failed"
    append = result["steps"]["append_daily"]
    assert [r["symbol"] for r in append["refused"]] == ["ETHA"] and "bin/tradeit refresh" in append["error"]
    assert list((disk.dir / "artifacts" / "runs").glob("daily_run_*.json"))
