"""Orders execute at the close the targets assume.

The daily run starts at 15:35 ET. Its targets come from the last completed
close (a bar for a session still trading is dropped), and its orders go
market-on-close (MOC) until 15:45 ET, or market-on-open (MKT with tif OPG) for
the next session after that, before the open, or on a day the market is
closed. An early close moves the deadline with the close. Every IBKR call is a
fake, and the clock is pinned with ``clock.now_et``.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.broker.ibkr import auction  # noqa: E402
from src.broker.ibkr.orders import IBKROrderError, IBKROrderManager, OrderRequest  # noqa: E402
from src.common import clock  # noqa: E402

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


def et(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=ET)


@pytest.fixture()
def at(monkeypatch: pytest.MonkeyPatch):
    """Pin the engine's clock: ``at(datetime)``."""

    def pin(when: datetime) -> datetime:
        monkeypatch.setattr(clock, "now_et", lambda: when.astimezone(ET))
        return when

    return pin


# ── the calendar ─────────────────────────────────────────────────────────────

def test_early_closes_are_the_nyse_ones() -> None:
    early = [d for d in pd.date_range("2025-01-01", "2027-12-31").date if clock.is_early_close(d)]
    # services/api/dv02_api/nyse.py EARLY_CLOSES; July 3 2026 and Dec 24 2027 are holidays
    assert early == [date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
                     date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)]  # fmt: skip
    session = clock.market_session_for(date(2026, 11, 27))
    assert session.close_at == et("2026-11-27", "13:00")
    assert clock.market_session_for(date(2026, 11, 30)).close_at == et("2026-11-30", "16:00")


@pytest.mark.parametrize(
    ("when", "completed"),
    [
        (et("2026-10-13", "15:35"), date(2026, 10, 12)),  # Tuesday before the close: Monday's
        (et("2026-10-13", "16:10"), date(2026, 10, 12)),  # closed, but the bar is still settling
        (et("2026-10-13", "16:20"), date(2026, 10, 13)),
        (et("2026-10-12", "08:00"), date(2026, 10, 9)),   # Monday before the open: Friday's
        (et("2026-10-17", "12:00"), date(2026, 10, 16)),  # Saturday
        (et("2026-11-27", "13:20"), date(2026, 11, 27)),  # early close at 13:00
        (et("2026-11-27", "12:00"), date(2026, 11, 25)),  # Thanksgiving in between
    ],
)
def test_the_last_completed_session(when: datetime, completed: date) -> None:
    assert clock.last_completed_session(when) == completed


# ── MOC or market-on-open, by the time the batch goes out ────────────────────

MODEL = ["SPY", "TLT", "GLD", "QQQ", "VIXY"]  # NYSE Arca, Nasdaq and Cboe BZX listings


@pytest.mark.parametrize(
    ("when", "order_type", "tif", "session", "fills_at"),
    [
        # Tuesday: before the 15:45 deadline, today's close
        (et("2026-10-13", "15:35"), "MOC", "DAY", date(2026, 10, 13), et("2026-10-13", "16:00")),
        (et("2026-10-13", "10:00"), "MOC", "DAY", date(2026, 10, 13), et("2026-10-13", "16:00")),
        (et("2026-10-13", "15:44"), "MOC", "DAY", date(2026, 10, 13), et("2026-10-13", "16:00")),
        # at or after the deadline, still in the session: the next open
        (et("2026-10-13", "15:45"), "MKT", "OPG", date(2026, 10, 14), et("2026-10-14", "09:30")),
        (et("2026-10-13", "15:58"), "MKT", "OPG", date(2026, 10, 14), et("2026-10-14", "09:30")),
        # market closed: after the close, and before the open
        (et("2026-10-13", "17:32"), "MKT", "OPG", date(2026, 10, 14), et("2026-10-14", "09:30")),
        (et("2026-10-13", "08:00"), "MKT", "OPG", date(2026, 10, 13), et("2026-10-13", "09:30")),
        # too late for the opening auction: that day's close
        (et("2026-10-13", "09:26"), "MOC", "DAY", date(2026, 10, 13), et("2026-10-13", "16:00")),
        # weekend, Friday after the deadline, and a holiday: Monday's open, Friday's open
        (et("2026-10-17", "12:00"), "MKT", "OPG", date(2026, 10, 19), et("2026-10-19", "09:30")),
        (et("2026-10-16", "15:50"), "MKT", "OPG", date(2026, 10, 19), et("2026-10-19", "09:30")),
        (et("2026-11-26", "15:35"), "MKT", "OPG", date(2026, 11, 27), et("2026-11-27", "09:30")),
        # early close (13:00): the deadline moves to 12:45, so the 15:35 run goes on the open
        (et("2026-11-27", "12:40"), "MOC", "DAY", date(2026, 11, 27), et("2026-11-27", "13:00")),
        (et("2026-11-27", "12:45"), "MKT", "OPG", date(2026, 11, 30), et("2026-11-30", "09:30")),
        (et("2026-11-27", "15:35"), "MKT", "OPG", date(2026, 11, 30), et("2026-11-30", "09:30")),
    ],
)
def test_the_order_type_follows_the_clock(
    when: datetime, order_type: str, tif: str, session: date, fills_at: datetime
) -> None:
    ex = auction.execution_for(MODEL, at=when)
    assert (ex.order_type, ex.tif, ex.session, ex.fills_at) == (order_type, tif, session, fills_at)
    assert ex.auction == ("close" if order_type == "MOC" else "open")
    assert (ex.moc_deadline is not None) == (order_type == "MOC")


def test_the_deadline_is_the_earliest_cutoff_of_the_batch() -> None:
    close = et("2026-10-13", "16:00")
    assert auction.moc_deadline(close, ["QQQ", "TLT", "VIXY"]) == et("2026-10-13", "15:50")  # Nasdaq, BZX: 15:55
    assert auction.moc_deadline(close, ["QQQ", "SPY"]) == et("2026-10-13", "15:45")  # NYSE Arca: 15:50
    assert auction.moc_deadline(close, ["ZZZZ"]) == et("2026-10-13", "15:45")  # unknown: NYSE Arca
    assert auction.moc_deadline(close) == et("2026-10-13", "15:45")
    nasdaq_only = auction.execution_for(["QQQ", "TLT"], at=et("2026-10-13", "15:47"))
    assert nasdaq_only.order_type == "MOC" and nasdaq_only.moc_deadline == et("2026-10-13", "15:50")


def test_a_central_time_clock_reads_the_same_instant() -> None:
    ex = auction.execution_for(MODEL, at=datetime(2026, 10, 13, 14, 35, tzinfo=CT))
    assert ex.order_type == "MOC" and ex.summary() == "market-on-close, filled at the Tue Oct 13 close"
    late = auction.execution_for(MODEL, at=datetime(2026, 10, 13, 16, 32, tzinfo=CT))
    assert late.summary() == "market-on-open, filled at the Wed Oct 14 open"


def test_without_a_time_it_reads_the_engines_clock(at) -> None:
    at(et("2026-10-13", "15:35"))
    assert auction.execution_for(MODEL).order_type == "MOC"
    at(et("2026-10-13", "15:46"))
    assert auction.execution_for(MODEL).tif == "OPG"


# ── the IBKR orders ──────────────────────────────────────────────────────────

def test_the_requests_become_moc_and_market_on_open_orders() -> None:
    manager = IBKROrderManager(SimpleNamespace(primary_account=lambda: "DU1234567"))
    moc = manager.build_order(OrderRequest("SPY", "BUY", 10, order_type="MOC", tif="DAY"))
    assert (moc.orderType, moc.tif, moc.outsideRth, moc.account) == ("MOC", "DAY", False, "DU1234567")
    moo = manager.build_order(OrderRequest("SPY", "SELL", 10, order_type="MKT", tif="OPG"))
    assert (moo.orderType, moo.tif, moo.outsideRth) == ("MKT", "OPG", False)
    with pytest.raises(IBKROrderError, match="MOC orders must use tif='DAY'"):
        OrderRequest("SPY", "BUY", 10, order_type="MOC", tif="OPG").validate()
    with pytest.raises(IBKROrderError, match="for MKT and LMT orders only"):
        OrderRequest("SPY", "BUY", 10, order_type="STP", stop_price=500.0, tif="OPG").validate()
    with pytest.raises(IBKROrderError, match="outside regular hours"):
        OrderRequest("SPY", "BUY", 10, order_type="MKT", tif="OPG", outside_rth=True).validate()


def orders_file(tmp_path: Path) -> Path:
    path = tmp_path / "paper_orders_only.parquet"
    pd.DataFrame(
        [
            {"symbol": "SPY", "side": "BUY", "delta_shares": 10, "estimated_trade_dollars": 5000.0},
            {"symbol": "XLE", "side": "SELL", "delta_shares": -300, "estimated_trade_dollars": 27000.0},
            {"symbol": "TLT", "side": "BUY", "delta_shares": 40, "estimated_trade_dollars": 4000.0},
            {"symbol": "DBC", "side": "SELL", "delta_shares": -5, "estimated_trade_dollars": 100.0},
        ]
    ).to_parquet(path, index=False)
    return path


@pytest.fixture()
def submit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """submit_paper_orders on a fake IBKR: the real request-to-Order mapping, no network.
    Returns ``send(dry_run)`` -> (result, placed orders, submission log)."""
    from src.production.runtime import submit_paper_orders as spo

    placed: list[tuple[str, str, str, str]] = []

    class FakeClient:
        def __init__(self, _config) -> None: ...
        def connect(self) -> None: ...
        def disconnect(self) -> None: ...
        def primary_account(self) -> str:
            return "DU1234567"

    class FakeManager(IBKROrderManager):
        def place_order(self, request, **_kw):
            order = self.build_order(request)
            placed.append((order.action, request.symbol, order.orderType, order.tif))
            return SimpleNamespace(orderStatus=SimpleNamespace(status="PreSubmitted"))

        @staticmethod
        def summarize_trade(_trade) -> dict:
            return {"order_id": len(placed), "perm_id": None}

    monkeypatch.setattr(spo, "IBKRClient", FakeClient)
    monkeypatch.setattr(spo, "IBKROrderManager", FakeManager)
    # how a batch goes out, not whether it may: the gate has its own tests (test_submit_gate.py)
    monkeypatch.setattr(spo, "pre_trade_blocks", lambda *_a, **_k: [])

    def send(dry_run: bool = False):
        placed.clear()
        out = tmp_path / f"run{len(list(tmp_path.glob('run*')))}"
        cfg = spo.SubmitPaperOrdersConfig(
            orders_path=orders_file(tmp_path), submissions_dir=out / "orders", fills_dir=out / "fills", dry_run=dry_run
        )
        result = spo.submit_paper_orders(cfg)
        log = pd.read_csv(next((out / "orders").glob("paper_orders_submitted_*.csv")))
        return result, list(placed), log

    return send


def test_before_the_deadline_every_order_goes_moc_sells_first(at, submit) -> None:
    at(et("2026-10-13", "15:36"))
    result, placed, log = submit()
    assert placed == [
        ("SELL", "XLE", "MOC", "DAY"), ("SELL", "DBC", "MOC", "DAY"),
        ("BUY", "SPY", "MOC", "DAY"), ("BUY", "TLT", "MOC", "DAY"),
    ]  # fmt: skip
    assert list(log["symbol"]) == ["XLE", "DBC", "SPY", "TLT"]
    assert set(zip(log["order_type"], log["tif"])) == {("MOC", "DAY")}
    assert result["execution"]["summary"] == "market-on-close, filled at the Tue Oct 13 close"
    assert result["execution"]["moc_deadline"] == "2026-10-13T15:45:00-04:00"


def test_after_the_deadline_every_order_goes_on_the_next_open_sells_first(at, submit) -> None:
    for when in (et("2026-10-13", "15:50"), et("2026-10-13", "17:32"), et("2026-10-10", "07:00")):
        at(when)  # after the deadline, after the close, and on a Saturday
        result, placed, log = submit()
        assert [(a, s) for a, s, _, _ in placed] == [("SELL", "XLE"), ("SELL", "DBC"), ("BUY", "SPY"), ("BUY", "TLT")]
        assert {(t, f) for _, _, t, f in placed} == {("MKT", "OPG")}  # never a plain market order
        assert set(zip(log["order_type"], log["tif"])) == {("MKT", "OPG")}
        assert result["execution"]["auction"] == "open"
    assert result["execution"]["session"] == "2026-10-12"  # Saturday's batch: Monday's open


def test_an_early_close_moves_the_deadline(at, submit) -> None:
    at(et("2026-11-27", "12:30"))
    assert {t for _, _, t, _ in submit()[1]} == {"MOC"}
    at(et("2026-11-27", "12:50"))
    result, placed, _ = submit()
    assert {(t, f) for _, _, t, f in placed} == {("MKT", "OPG")}
    assert result["execution"]["session"] == "2026-11-30"


def test_a_dry_run_says_how_the_orders_would_go(at, submit) -> None:
    at(et("2026-10-13", "15:36"))
    result, placed, log = submit(dry_run=True)
    assert placed == [] and result["submitted"] is False
    assert result["execution"]["order_type"] == "MOC"
    assert list(log["symbol"]) == ["XLE", "DBC", "SPY", "TLT"] and set(log["tif"]) == {"DAY"}


# ── before the close, the run reads yesterday's close ────────────────────────

SYMBOLS = ("SPY", "TLT", "GLD")
TUESDAY = date(2026, 10, 13)


def history(through: date = TUESDAY) -> pd.DataFrame:
    """Daily bars through ``through``; before that day's close its bar is the partial one."""
    dates = pd.bdate_range("2025-06-02", pd.Timestamp(through))
    rng = np.random.default_rng(11)
    frames = []
    for i, sym in enumerate(SYMBOLS):
        close = (100 + 50 * i) * np.exp(np.cumsum(rng.normal(0.0003, 0.008, len(dates))))
        frames.append(pd.DataFrame({"date": dates, "symbol": sym, "open": close, "high": close, "low": close,
                                    "close": close, "volume": 1e6}))  # fmt: skip
    return pd.concat(frames, ignore_index=True)


class FakeIB:
    """Just enough of ib_async.IB for append_ibkr_daily: TRADES daily bars that, like
    IBKR's during a session, include today's bar so far."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self.bars = bars

    def qualifyContracts(self, contract):  # noqa: N802
        return [contract]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate):  # noqa: N802,N803
        n, unit = durationStr.split()
        days = int(n) * {"D": 1, "W": 7, "M": 31, "Y": 366}[unit]
        rows = self.bars[(self.bars["symbol"] == contract.symbol)
                         & (self.bars["date"] > pd.Timestamp(TUESDAY) - pd.Timedelta(days=days))]  # fmt: skip
        return [SimpleNamespace(date=r.date.date(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
                for r in rows.itertuples(index=False)]  # fmt: skip

    def disconnect(self) -> None: ...


@pytest.fixture()
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The append step and target generation on a temp price master that ends Friday."""
    from src.production import generate_targets as gt
    from src.production.pipeline import append_ibkr_daily as app

    prices = tmp_path / "data" / "market" / "cleaned" / "prices"
    targets = tmp_path / "data" / "market" / "cleaned" / "targets"
    prices.mkdir(parents=True)
    monkeypatch.setattr(app, "MASTER_CSV", prices / "etf_prices_master.csv")
    monkeypatch.setattr(app, "MASTER_PARQUET", prices / "etf_prices_master.parquet")
    monkeypatch.setattr(app, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    paths = {
        "prices_csv": prices / "etf_prices_master.csv",
        "prices_parquet": prices / "etf_prices_master.parquet",
        "targets_csv": targets / "etf_targets_monthly.csv",
        "targets_parquet": targets / "etf_targets_monthly.parquet",
    }
    monkeypatch.setattr(gt, "_paths", lambda: paths)
    monkeypatch.setenv("CAPITALFUND_OPTIMIZER", "inverse_vol")
    bars = history()
    master = bars[bars["date"] <= pd.Timestamp("2026-10-09")]
    master.to_parquet(paths["prices_parquet"], index=False)
    monkeypatch.setattr(app, "_connect", lambda host, port, client_id: FakeIB(bars))
    return SimpleNamespace(root=tmp_path, paths=paths, bars=bars, app=app, gt=gt)


def close_on(df: pd.DataFrame, symbol: str, day: date) -> float:
    return float(df.loc[(df["symbol"] == symbol) & (pd.to_datetime(df["date"]).dt.date == day), "close"].iloc[0])


def test_the_append_drops_todays_partial_bar_before_the_close(at, tree) -> None:
    at(et("2026-10-13", "15:35"))
    result = tree.app.append_ibkr_daily(profile="paper", lookback="5 D")
    master = pd.read_parquet(tree.paths["prices_parquet"])
    assert result["ok"] is True and result["latest_date"] == "2026-10-12"
    assert result["completed_through"] == "2026-10-12" and result["partial_bars_dropped"] == len(SYMBOLS)
    assert pd.to_datetime(master["date"]).max() == pd.Timestamp("2026-10-12")

    at(et("2026-10-13", "16:30"))  # after the close the day's bar is final, and kept
    result = tree.app.append_ibkr_daily(profile="paper", lookback="5 D")
    assert result["latest_date"] == "2026-10-13" and result["partial_bars_dropped"] == 0


def test_targets_never_read_a_session_still_trading(at, tree) -> None:
    # a partial bar already in the master (an intraday refresh from before this fix, say)
    tree.bars.to_parquet(tree.paths["prices_parquet"], index=False)
    at(et("2026-10-13", "15:35"))
    result = tree.gt.generate_targets(universe=SYMBOLS)
    targets = pd.read_parquet(tree.paths["targets_parquet"])
    latest = targets.loc[targets["rebalance_date"] == targets["rebalance_date"].max()]
    assert result["prices_through"] == "2026-10-12" and result["latest_rebalance_date"] == "2026-10-12"
    for sym in SYMBOLS:
        assert latest.set_index("symbol").loc[sym, "reference_price"] == pytest.approx(
            close_on(tree.bars, sym, date(2026, 10, 12))
        )


def test_the_runner_trades_on_yesterdays_close_before_the_close(at, tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 15:35 ET run end to end on a fake IB: bars and targets stop at Monday's
    close although IBKR already has Tuesday's bar so far."""
    from src.production import daily_runner

    at(et("2026-10-13", "15:35"))
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tree.root)
    monkeypatch.setattr(daily_runner, "_today", lambda: TUESDAY)
    monkeypatch.setattr(daily_runner, "_current_positions", lambda *a, **k: (None, None, "none"))
    monkeypatch.setattr(daily_runner, "_load_rebalance_config",
                        lambda: {**daily_runner.DEFAULT_REBALANCE_CONFIG, "cadence": "monthly"})  # fmt: skip
    monkeypatch.setattr("src.production.generate_targets.model_universe", lambda: SYMBOLS)
    for step in ("step_append_indices_daily", "step_append_futures_daily"):
        monkeypatch.setattr(daily_runner, step, lambda **kw: {"ok": True, "skipped": True})
    sent: list[str] = []
    monkeypatch.setattr(daily_runner, "step_build_basket", lambda **kw: {"ok": True})
    monkeypatch.setattr(daily_runner, "step_submit_orders", lambda **kw: sent.append("submit") or {"ok": True})

    result = daily_runner.run(lookback="5 D", force_rebalance=True)

    assert result["ok"] is True and sent == ["submit"]
    steps = result["steps"]
    assert steps["append_daily"]["latest_date"] == "2026-10-12"
    assert steps["generate_targets"]["latest_rebalance_date"] == "2026-10-12"
    assert steps["generate_targets"]["prices_through"] == "2026-10-12"
    master = pd.read_parquet(tree.paths["prices_parquet"])
    assert TUESDAY not in set(pd.to_datetime(master["date"]).dt.date)
    # and the orders it sends now fill at today's close
    assert auction.execution_for(SYMBOLS).summary() == "market-on-close, filled at the Tue Oct 13 close"


def test_a_run_before_the_open_reads_the_previous_close(at, tree) -> None:
    tree.bars.to_parquet(tree.paths["prices_parquet"], index=False)  # rows through Tuesday
    at(et("2026-10-13", "08:00") - timedelta(days=1))  # Monday 08:00: Friday's close
    assert tree.gt.generate_targets(universe=SYMBOLS)["prices_through"] == "2026-10-09"


def test_a_basis_repair_before_the_close_leaves_todays_bar_out_too(at, tree) -> None:
    old_basis = tree.bars[tree.bars["date"] <= pd.Timestamp("2026-10-09")].copy()
    old_basis.loc[old_basis["symbol"] == "SPY", ["open", "high", "low", "close"]] *= 3  # before a reverse split
    old_basis.to_parquet(tree.paths["prices_parquet"], index=False)
    at(et("2026-10-13", "15:35"))
    result = tree.app.repair_ibkr_basis(symbols=["SPY"])
    master = pd.read_parquet(tree.paths["prices_parquet"])
    spy = master.loc[master["symbol"] == "SPY"]
    assert result["refetched"] == ["SPY"]
    assert pd.to_datetime(spy["date"]).max() == pd.Timestamp("2026-10-12")
