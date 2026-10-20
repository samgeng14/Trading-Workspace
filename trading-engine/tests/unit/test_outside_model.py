"""Holdings outside the model are sold at the next rebalance (owner's decision, 2026-10-12).

The basket (build_reconciliation) sells every share holding the targets don't
include, whole, priced at IBKR's mark, else the master's last close, else left
unpriced for the no-leverage check to block. Futures and options outside the
model are kept and named in a warning. The daily runner rebalances while any
such shares are held, and every order path sends its sells before its buys.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production.runtime.build_paper_basket import (  # noqa: E402
    build_reconciliation,
    holdings_outside_model,
)

NAV = 1_000_000.0
TODAY = date(2026, 10, 13)


def targets(weights: dict[str, float], closes: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp("2026-10-09"), "symbol": s, "target_weight": w, "close": closes[s]}
         for s, w in weights.items()]
    )


def position(symbol: str, qty: float, mark: float | None, sec_type: str = "STK", multiplier: float = 1.0) -> dict:
    """A row of the positions snapshot (build_paper_basket.fetch_positions_df)."""
    return {"symbol": symbol, "current_shares": qty, "avg_cost": 1.0, "sec_type": sec_type,
            "multiplier": multiplier, "market_price": math.nan if mark is None else mark}


MODEL = targets({"SPY": 0.5, "TLT": 0.5}, {"SPY": 500.0, "TLT": 100.0})


def book(*extra: dict) -> pd.DataFrame:
    return pd.DataFrame([position("SPY", 900, 500.0), position("TLT", 4500, 100.0), *extra])


def row(recon: pd.DataFrame, symbol: str) -> dict:
    return recon.set_index("symbol").loc[symbol].to_dict()


# ── the basket ───────────────────────────────────────────────────────────────

def test_shares_outside_the_model_are_sold_whole_at_the_mark_else_the_master_else_unpriced() -> None:
    positions = book(
        position("XLE", 300, 90.0),  # IBKR has a mark: it wins over the master's 88
        position("DBC", 5, None),  # no mark: the master's last close; $100, under the $1,000 minimum
        position("ZZZ", 100, None),  # no price anywhere: left unpriced, never guessed
        position("MESZ6", -5, 6650.0, "FUT", 5.0),
        position("SPY   261218C00600000", -10, 4.2, "OPT", 100.0),
    )
    recon, orders = build_reconciliation(
        MODEL, positions, NAV, cash_buffer=0.005, min_trade_dollars=1000.0,
        last_closes={"SPY": 499.0, "XLE": 88.0, "DBC": 20.0},
    )

    for sym, qty in (("XLE", 300), ("DBC", 5), ("ZZZ", 100)):
        r = row(recon, sym)
        assert not r["in_model"]
        assert (r["target_weight"], r["target_shares"], r["current_shares"]) == (0.0, 0, qty)
        assert (r["delta_shares"], r["side"]) == (-qty, "SELL")
    assert row(recon, "XLE")["close"] == 90.0
    assert row(recon, "DBC")["close"] == 20.0
    assert math.isnan(row(recon, "ZZZ")["close"])

    # every one goes out whole, the $100 DBC and the unpriced ZZZ included
    sells = orders.set_index("symbol")
    assert {"XLE", "DBC", "ZZZ"} <= set(sells.index)
    assert sells.loc["DBC", "delta_shares"] == -5 and sells.loc["DBC", "estimated_trade_dollars"] == 100.0
    # futures and options are left alone, in neither file
    assert not {"MESZ6", "SPY   261218C00600000"} & set(recon["symbol"])
    # the model's own rows are unchanged: SPY 900 -> 995 shares, TLT 4500 -> 4975
    assert row(recon, "SPY")["in_model"] and row(recon, "SPY")["delta_shares"] == 95
    assert row(recon, "TLT")["delta_shares"] == 475


def test_futures_and_options_outside_the_model_are_kept_with_a_warning() -> None:
    positions = book(
        position("XLE", 300, 90.0),
        position("MESZ6", -5, 6650.0, "FUT", 5.0),
        position("SPY   261218C00600000", -10, 4.2, "OPT", 100.0),
    )
    outside = holdings_outside_model(MODEL, positions)
    assert outside.sell == ["XLE"]
    assert outside.kept == ["MESZ6", "SPY   261218C00600000"]
    (warning,) = outside.warnings()
    assert warning.startswith("MESZ6, SPY   261218C00600000: outside the model, but not sold automatically")
    assert "until you close them" in warning and "no-leverage check" in warning
    assert holdings_outside_model(MODEL, book()).warnings() == []


def test_a_short_outside_the_model_is_bought_back() -> None:
    recon, orders = build_reconciliation(MODEL, book(position("XLE", -50, 90.0)), NAV, min_trade_dollars=1000.0)
    assert (row(recon, "XLE")["delta_shares"], row(recon, "XLE")["side"]) == (50, "BUY")
    assert "XLE" in set(orders["symbol"])


def test_a_target_left_in_an_old_targets_file_is_not_bought_back() -> None:
    # targets written before TQQQ left the model still list it; the basket sells it instead
    stale = targets({"SPY": 0.475, "TLT": 0.478, "TQQQ": 0.047}, {"SPY": 500.0, "TLT": 100.0, "TQQQ": 100.0})
    recon, orders = build_reconciliation(
        stale, book(position("TQQQ", 400, 101.0)), NAV, cash_buffer=0.005, model=("SPY", "TLT", "QQQ")
    )
    tqqq = row(recon, "TQQQ")
    assert tqqq["target_weight"] == 0.0 and tqqq["delta_shares"] == -400 and tqqq["close"] == 101.0
    assert orders.set_index("symbol").loc["TQQQ", "side"] == "SELL"
    # and with no TQQQ held, nothing buys it
    recon, _ = build_reconciliation(stale, book(), NAV, model=("SPY", "TLT", "QQQ"))
    assert "TQQQ" not in set(recon["symbol"])


def test_a_fraction_of_a_share_is_kept_rather_than_sold_every_day() -> None:
    # the basket trades whole shares: 10.4 sells 10, and the 0.4 left over is reported, not retried
    recon, _ = build_reconciliation(MODEL, book(position("XLE", 10.4, 90.0)), NAV)
    assert row(recon, "XLE")["delta_shares"] == -10
    outside = holdings_outside_model(MODEL, book(position("XLE", 0.4, 90.0)))
    assert outside.sell == [] and outside.kept == ["XLE"]


def test_the_whitelist_limits_the_sells_too() -> None:
    positions = book(position("XLE", 300, 90.0), position("DBC", 500, 20.0))
    recon, _ = build_reconciliation(MODEL, positions, NAV, symbol_whitelist=("SPY", "XLE"))
    assert set(recon["symbol"]) == {"SPY", "XLE"}


def test_build_paper_basket_reports_what_it_sells_and_keeps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.production.runtime import build_paper_basket as bpb

    model = ("SPY", "TLT")
    monkeypatch.setattr("src.production.generate_targets.model_universe", lambda: model)
    targets_path = tmp_path / "targets.parquet"
    MODEL.rename(columns={"date": "rebalance_date", "close": "reference_price"}).to_parquet(targets_path)
    prices_path = tmp_path / "etf_prices_master.parquet"
    pd.DataFrame(
        [{"date": pd.Timestamp("2026-10-08"), "symbol": "DBC", "close": 19.0},
         {"date": pd.Timestamp("2026-10-09"), "symbol": "DBC", "close": 20.0}]
    ).to_parquet(prices_path)

    held = [
        (SimpleNamespace(conId=i, symbol=s, localSymbol=local, secType=t, multiplier=m), q, px)
        for i, (s, local, t, m, q, px) in enumerate(
            [("SPY", "SPY", "STK", "", 900, 500.0), ("TLT", "TLT", "STK", "", 4500, 100.0),
             ("DBC", "DBC", "STK", "", 50, float("nan")), ("MES", "MESZ6", "FUT", "5", -5, 6650.0)], start=1)
    ]  # fmt: skip

    class FakeClient:
        def __init__(self, _config) -> None:
            self.ib = SimpleNamespace(portfolio=lambda: [SimpleNamespace(contract=c, marketPrice=px) for c, _, px in held])

        def connect(self) -> None: ...
        def disconnect(self) -> None: ...

        def positions(self):
            return [SimpleNamespace(contract=c, position=q, avgCost=1.0) for c, q, _ in held]

        def account_summary(self):
            return {"NetLiquidation": str(NAV), "TotalCashValue": "50000"}

        def net_liquidation(self) -> float:
            return NAV

    monkeypatch.setattr(bpb, "IBKRClient", FakeClient)
    out = tmp_path / "out"
    cfg = bpb.BasketBuildConfig(
        targets_path=targets_path,
        reconciliation_csv_path=out / "recon.csv",
        reconciliation_parquet_path=out / "recon.parquet",
        blotter_csv_path=out / "blotter.csv",
        blotter_parquet_path=out / "blotter.parquet",
        orders_csv_path=out / "orders.csv",
        orders_parquet_path=out / "orders.parquet",
        account_snapshot_dir=out / "account",
        positions_snapshot_dir=out / "positions",
        prices_path=prices_path,
    )
    result = bpb.build_paper_basket(cfg)

    assert result["outside_model_sold"] == ["DBC"] and result["outside_model_kept"] == ["MESZ6"]
    assert result["warnings"] and result["warnings"][0].startswith("MESZ6: outside the model")
    orders = pd.read_parquet(cfg.orders_parquet_path).set_index("symbol")
    # $1,000 of DBC: priced at the master's last close, since IBKR had no mark
    assert orders.loc["DBC", "delta_shares"] == -50 and orders.loc["DBC", "close"] == 20.0
    assert result["sell_dollars"] == pytest.approx(1000.0)


# ── sells before buys ────────────────────────────────────────────────────────

def orders_file(tmp_path: Path) -> Path:
    path = tmp_path / "paper_orders_only.parquet"
    pd.DataFrame(
        [
            {"symbol": "SPY", "side": "BUY", "delta_shares": 10, "estimated_trade_dollars": 5000.0},
            {"symbol": "XLE", "side": "SELL", "delta_shares": -300, "estimated_trade_dollars": 27000.0},
            {"symbol": "TLT", "side": "BUY", "delta_shares": 40, "estimated_trade_dollars": 4000.0},
            {"symbol": "DBC", "side": "SELL", "delta_shares": -5, "estimated_trade_dollars": 100.0},
            {"symbol": "GLD", "side": "HOLD", "delta_shares": 0, "estimated_trade_dollars": 0.0},
        ]
    ).to_parquet(path, index=False)
    return path


def test_orders_are_sent_sells_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.production.runtime import submit_paper_orders as spo

    path = orders_file(tmp_path)
    loaded = spo.load_orders(path)
    assert list(zip(loaded["side"], loaded["symbol"])) == [
        ("SELL", "XLE"), ("SELL", "DBC"), ("BUY", "SPY"), ("BUY", "TLT"),
    ]  # fmt: skip

    placed: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, _config) -> None: ...
        def connect(self) -> None: ...
        def disconnect(self) -> None: ...

    class FakeManager:
        def __init__(self, _client) -> None: ...

        def place_order(self, request, **_kw):
            placed.append((request.action, request.symbol))
            return SimpleNamespace(orderStatus=SimpleNamespace(status="Submitted"))

        def summarize_trade(self, _trade) -> dict:
            return {"order_id": len(placed), "perm_id": None}

    monkeypatch.setattr(spo, "IBKRClient", FakeClient)
    monkeypatch.setattr(spo, "IBKROrderManager", FakeManager)
    monkeypatch.setattr(spo, "pre_trade_blocks", lambda *_a, **_k: [])
    monkeypatch.setattr(spo, "working_orders", lambda *_a, **_k: [])  # the gate has its own tests
    cfg = spo.SubmitPaperOrdersConfig(
        orders_path=path, submissions_dir=tmp_path / "orders", fills_dir=tmp_path / "fills", dry_run=False
    )
    spo.submit_paper_orders(cfg)
    assert placed == [("SELL", "XLE"), ("SELL", "DBC"), ("BUY", "SPY"), ("BUY", "TLT")]
    submitted = pd.read_csv(next((tmp_path / "orders").glob("paper_orders_submitted_*.csv")))
    assert list(submitted["symbol"]) == ["XLE", "DBC", "SPY", "TLT"]
    assert list(submitted["delta_shares"]) == [-300, -5, 10, 40]  # each row still matches its order


# ── the daily runner rebalances while shares outside the model are held ──────

DRIFT = {
    "cadence": "drift",
    "drift_threshold": 0.05,
    "composition_threshold": 0.05,
    "cooldown_days": 3,
    "state_path": "data/broker/state/last_rebalance.json",
}


@pytest.fixture()
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The daily runner on a temp tree: targets, positions on target (no drift), a
    prior basket, and every step after the decision stubbed out."""
    from src.production import daily_runner

    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: TODAY)
    monkeypatch.setattr(daily_runner, "_load_rebalance_config", lambda: dict(DRIFT))
    monkeypatch.setattr(daily_runner, "step_generate_targets", lambda profile: {"ok": True})
    called: list[str] = []
    for step in ("step_build_basket", "step_submit_orders"):
        monkeypatch.setattr(daily_runner, step, lambda _s=step, **kw: called.append(_s) or {"ok": True, "skipped": True})

    tdir = tmp_path / "data" / "market" / "cleaned" / "targets"
    tdir.mkdir(parents=True)
    weights = {"SPY": 0.5, "TLT": 0.5}
    pd.DataFrame(
        [{"rebalance_date": pd.Timestamp("2026-10-09"), "symbol": s, "target_weight": w} for s, w in weights.items()]
    ).to_parquet(tdir / "etf_targets_monthly.parquet")
    rdir = tmp_path / "data" / "broker" / "reconciliations"
    rdir.mkdir(parents=True)
    pd.DataFrame(
        [{"symbol": "SPY", "target_weight": 0.5, "target_dollars": 497_500.0, "current_shares": 995, "close": 500.0},
         {"symbol": "TLT", "target_weight": 0.5, "target_dollars": 497_500.0, "current_shares": 4975, "close": 100.0}]
    ).to_parquet(rdir / "paper_reconciliation.parquet")

    def state(submitted_at: date) -> None:
        path = tmp_path / DRIFT["state_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"submitted_at": submitted_at.isoformat(), "basket": weights}))

    def holding(*rows: dict) -> None:
        positions = pd.DataFrame([position("SPY", 995, 500.0), position("TLT", 4975, 100.0), *rows])
        monkeypatch.setattr(daily_runner, "_current_positions", lambda *a, **k: (positions, NAV, "ibkr"))

    state(date(2026, 10, 1))
    return SimpleNamespace(module=daily_runner, called=called, state=state, holding=holding)


def test_the_runner_rebalances_while_shares_outside_the_model_are_held(runner) -> None:
    runner.holding(position("XLE", 300, 90.0), position("MESZ6", -5, 6650.0, "FUT", 5.0))
    result = runner.module.run(skip_append=True, dry_run=True)

    assert result["should_rebalance"] is True
    decision = result["rebalance_decision"]
    assert decision["reason"] == "holdings outside the model: XLE"
    assert decision["outside_model"]["sell"] == ["XLE"] and decision["outside_model"]["positions_from"] == "ibkr"
    # the future is kept, and the run log says so
    assert decision["outside_model"]["kept"] == ["MESZ6"]
    assert "MESZ6: outside the model, but not sold automatically" in decision["outside_model"]["warnings"][0]
    assert runner.called == ["step_build_basket", "step_submit_orders"]


def test_without_outside_holdings_the_drift_gate_decides_as_before(runner) -> None:
    # a future outside the model is not a reason to rebalance: the basket can't sell it
    runner.holding(position("MESZ6", -5, 6650.0, "FUT", 5.0))
    result = runner.module.run(skip_append=True, dry_run=True)
    assert result["should_rebalance"] is False and "within band" in result["rebalance_decision"]["reason"]
    assert runner.called == []


def test_the_cooldown_still_holds_back_a_second_sell(runner) -> None:
    # submitted yesterday: IBKR's positions don't show a sell still working, so don't send it twice
    runner.state(date(2026, 10, 12))
    runner.holding(position("XLE", 300, 90.0))
    result = runner.module.run(skip_append=True, dry_run=True)
    assert result["should_rebalance"] is False
    assert result["rebalance_decision"]["reason"] == "holdings outside the model: XLE, but in cooldown (2d remaining)"
    assert runner.called == []


def test_monthly_cadence_rebalances_for_outside_holdings_too(runner, monkeypatch: pytest.MonkeyPatch) -> None:
    # the latest signal (2026-10-09) is this month's, so monthly alone would skip
    monkeypatch.setattr(runner.module, "_load_rebalance_config", lambda: {**DRIFT, "cadence": "monthly"})
    runner.holding(position("XLE", 300, 90.0))
    result = runner.module.run(skip_append=True, dry_run=True)
    assert result["should_rebalance"] is True
    assert result["rebalance_decision"]["reason"] == "holdings outside the model: XLE"


def test_positions_come_from_ibkr_else_the_last_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.broker.ibkr import client as ibkr_client
    from src.production import daily_runner

    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)

    class Down:
        def __init__(self, _config) -> None: ...

        def connect(self) -> None:
            raise ConnectionRefusedError("nothing is listening on 127.0.0.1:4002")

    monkeypatch.setattr(ibkr_client, "IBKRClient", Down)
    assert daily_runner._current_positions("paper", "127.0.0.1", 4002, 111) == (None, None, "none")

    snap = tmp_path / "data" / "broker" / "positions" / "paper_positions_snapshot.csv"
    snap.parent.mkdir(parents=True)
    pd.DataFrame([position("XLE", 300, 90.0)]).to_csv(snap, index=False)
    positions, _nav, source = daily_runner._current_positions("paper", "127.0.0.1", 4002, 111)
    assert source == "snapshot" and list(positions["symbol"]) == ["XLE"]
