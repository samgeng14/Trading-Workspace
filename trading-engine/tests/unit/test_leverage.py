"""The no-leverage check on the whole book (src/runtime/leverage.py) and its place in the runner."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.risk")

from src.runtime.leverage import check_leverage  # noqa: E402

NAV = 1_000_000.0
# the model's five ETFs at 18% of NAV each; their closes are in the reconciliation.
# IBKR has a mark for each but QQQ, which then prices at the reconciliation's close
MODEL = {"SPY": 500.0, "QQQ": 400.0, "IWM": 200.0, "TLT": 100.0, "GLD": 250.0}
MODEL_BOOK = [(s, 180_000 / px, None if s == "QQQ" else px) for s, px in MODEL.items()]
# held from before the model, outside it: DBC priced only by the master, XLE by IBKR's mark
OUTSIDE = [("DBC", 5_000, None), ("XLE", 300, 90.0)]


def write_run(
    root: Path,
    positions: list[tuple[str, float, float | None]],
    orders: list[tuple[str, int]],
    *,
    cash: float,
    nav: float = NAV,
) -> Path:
    """The files a run leaves on disk after build_basket. ``positions`` are
    (symbol, shares, IBKR mark or None); ``orders`` are (symbol, signed shares)."""
    broker = root / "data" / "broker"
    for sub in ("positions", "account", "orders", "reconciliations"):
        (broker / sub).mkdir(parents=True, exist_ok=True)
    snapshot = [
        {"symbol": s, "current_shares": q, "avg_cost": px or 1.0, "sec_type": "STK", "multiplier": 1.0,
         "market_price": px}
        for s, q, px in positions
    ]
    pd.DataFrame(snapshot).to_csv(broker / "positions" / "paper_positions_snapshot.csv", index=False)
    (broker / "account" / "paper_account_summary_20261008_163210.csv").write_text(
        f"tag,value\nNetLiquidation,{nav - 5_000}\nTotalCashValue,0\n"
    )
    (broker / "account" / "paper_account_summary_20261009_163210.csv").write_text(
        f"tag,value\nNetLiquidation,{nav}\nTotalCashValue,{cash}\n"
    )
    delta = dict(orders)
    recon = pd.DataFrame(
        [
            {
                "symbol": s,
                "close": px,
                "target_weight": 0.199,
                "delta_shares": delta.get(s, 0),
                "side": "BUY" if delta.get(s, 0) > 0 else "SELL" if delta.get(s, 0) < 0 else "HOLD",
                "estimated_trade_dollars": abs(delta.get(s, 0)) * px,
                "estimated_commission": max(1.0, 0.01 * abs(delta.get(s, 0))) if delta.get(s) else 0.0,
            }
            for s, px in MODEL.items()
        ]
    )
    recon.to_parquet(broker / "reconciliations" / "paper_reconciliation.parquet", index=False)
    orders_path = broker / "orders" / "paper_orders_only.parquet"
    recon.loc[recon["side"] != "HOLD"].reset_index(drop=True).to_parquet(orders_path, index=False)

    prices = root / "data" / "market" / "cleaned" / "prices"
    prices.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=3)
    closes = {**MODEL, "DBC": 20.0, "XLE": 88.0}
    rows = [
        {"date": d, "symbol": s, "close": px * (0.99 + 0.005 * i)}
        for s, px in closes.items()
        for i, d in enumerate(dates)
    ]
    pd.DataFrame(rows).to_parquet(prices / "etf_prices_master.parquet", index=False)
    return orders_path


def levered_book() -> list[tuple[str, float, float | None]]:
    # 90% in the model + 12.7% outside it = 102.7% of NAV, on a $27,000 margin loan
    return MODEL_BOOK + OUTSIDE


def run(root: Path, orders_path: Path):
    return check_leverage("paper", root=root, orders_path=orders_path)


def test_a_levered_rebalance_blocks(tmp_path: Path) -> None:
    path = write_run(tmp_path, levered_book(), [("TLT", 200), ("SPY", -20)], cash=-27_000)
    res = run(tmp_path, path)
    assert not res.ok
    text = " ".join(res.blocking_reasons)
    assert "gross 103.7% of NAV" in text and "margin loan" in text
    assert all(r.startswith("no leverage: ") for r in res.blocking_reasons)
    assert res.details["orders"] == 2
    assert "BLOCK" in res.report() and "102.7% -> 103.7%" in res.report()


def test_an_unlevered_rebalance_passes(tmp_path: Path) -> None:
    path = write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    res = run(tmp_path, path)
    assert res.ok, res.blocking_reasons
    assert res.blocking_reasons == [] and res.warnings == []
    assert res.details["after"]["gross"] == pytest.approx(0.94, abs=1e-4)
    assert res.details["after"]["cash"] == pytest.approx(100_000 - 40_000 - 4.0)


def test_holdings_outside_the_model_are_priced_and_counted(tmp_path: Path) -> None:
    path = write_run(tmp_path, levered_book(), [], cash=-27_000)
    res = run(tmp_path, path)
    sources = res.details["price_sources"]
    assert sources["DBC"] == "master" and sources["XLE"] == "ibkr"
    assert sources["SPY"] == "ibkr" and sources["QQQ"] == "reconciliation"
    # DBC at the master's last close (20.0 * 1.0), XLE at IBKR's mark, not the master's 88
    assert res.details["before"]["long_value"] == pytest.approx(900_000 + 5_000 * 20.0 + 300 * 90.0)
    assert res.details["before"]["gross"] == pytest.approx(1.027)
    # nothing to send: the levered book is reported, not blocked
    assert res.ok and any("gross 102.7%" in w for w in res.warnings)


def test_a_future_counts_at_notional(tmp_path: Path) -> None:
    path = write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    snapshot = path.parents[1] / "positions" / "paper_positions_snapshot.csv"
    df = pd.read_csv(snapshot)
    mes = {"symbol": "MES", "current_shares": -5, "avg_cost": 33_000.0, "sec_type": "FUT", "multiplier": 5.0,
           "market_price": 6650.0}
    pd.concat([df, pd.DataFrame([mes])]).to_csv(snapshot, index=False)
    res = run(tmp_path, path)
    assert res.details["before"]["short_value"] == pytest.approx(5 * 5 * 6650.0)
    assert res.details["after"]["cash"] == pytest.approx(100_000 - 40_000 - 4.0)  # futures move no cash
    assert not res.ok and any("gross 110.6%" in r for r in res.blocking_reasons)

    mes["multiplier"] = None  # can't be measured without it
    pd.concat([df, pd.DataFrame([mes])]).to_csv(snapshot, index=False)
    res = run(tmp_path, path)
    assert any("no usable price for MES" in r for r in res.blocking_reasons)


def held(con_id: int, symbol: str, sec_type: str, qty: float, mark: float | None, *, multiplier: str = "",
         local: str = "") -> tuple[SimpleNamespace, SimpleNamespace]:
    """An IBKR position and its portfolio item, as ib_async reports them."""
    contract = SimpleNamespace(conId=con_id, symbol=symbol, localSymbol=local or symbol, secType=sec_type,
                               multiplier=multiplier)
    return SimpleNamespace(contract=contract, position=qty, avgCost=1.0), SimpleNamespace(
        contract=contract, marketPrice=mark if mark is not None else float("nan"))


def snapshot_from_ibkr(root: Path, *items: tuple[SimpleNamespace, SimpleNamespace]) -> None:
    """The positions snapshot build_paper_basket writes for these IBKR positions."""
    from src.production.runtime.build_paper_basket import fetch_positions_df

    client = SimpleNamespace(
        positions=lambda: [p for p, _ in items], ib=SimpleNamespace(portfolio=lambda: [m for _, m in items])
    )
    path = root / "data" / "broker" / "positions" / "paper_positions_snapshot.csv"
    fetch_positions_df(client).to_csv(path, index=False)


def test_futures_and_options_from_ibkr_are_measured_per_contract(tmp_path: Path) -> None:
    path = write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    model = [held(i, s, "STK", q, px or MODEL[s]) for i, (s, q, px) in enumerate(MODEL_BOOK, start=1)]

    # a short MES at its full notional: 5 x 5 x 6650
    snapshot_from_ibkr(tmp_path, *model, held(50, "MES", "FUT", -5, 6650.0, multiplier="5", local="MESZ6"))
    res = run(tmp_path, path)
    assert res.details["before"]["short_value"] == pytest.approx(166_250.0)
    assert not res.ok and any("gross 110.6%" in r for r in res.blocking_reasons)

    # a future IBKR reports without a multiplier isn't counted as one share of the index: it blocks
    snapshot_from_ibkr(tmp_path, *model, held(50, "MES", "FUT", -1, 6650.0, local="MESZ6"))
    res = run(tmp_path, path)
    assert not res.ok and any("no usable price for MESZ6" in r for r in res.blocking_reasons)

    # two expiries, long one and short the other, are two positions, not a flat MES
    snapshot_from_ibkr(
        tmp_path, *model,
        held(50, "MES", "FUT", -5, 6650.0, multiplier="5", local="MESZ6"),
        held(51, "MES", "FUT", 5, 6700.0, multiplier="5", local="MESH7"),
    )  # fmt: skip
    res = run(tmp_path, path)
    assert res.details["before"]["gross"] == pytest.approx(0.9 + (166_250 + 167_500) / NAV)
    assert not res.ok

    # options on a model ETF are their own row: they block, and the ETF's shares stay its shares
    snapshot_from_ibkr(
        tmp_path, *model, held(60, "SPY", "OPT", -10, 4.2, multiplier="100", local="SPY   261218C00600000")
    )
    res = run(tmp_path, path)
    assert not res.ok and any("options (SPY   261218C00600000)" in r for r in res.blocking_reasons)
    snapshot = pd.read_csv(path.parents[1] / "positions" / "paper_positions_snapshot.csv")
    assert snapshot.set_index("symbol").loc["SPY", "current_shares"] == pytest.approx(360.0)


def test_an_unpriced_holding_blocks(tmp_path: Path) -> None:
    path = write_run(tmp_path, MODEL_BOOK + [("ZZZ", 100, None)], [("TLT", 10)], cash=100_000)
    res = run(tmp_path, path)
    assert not res.ok
    assert res.details["price_sources"]["ZZZ"] == "none"
    assert any("no usable price for ZZZ" in r for r in res.blocking_reasons)


def test_missing_engine_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    monkeypatch.setitem(sys.modules, "dv02_engine", None)
    monkeypatch.setitem(sys.modules, "dv02_engine.risk", None)
    res = run(tmp_path, path)
    assert not res.ok
    assert sys.executable in res.blocking_reasons[0] and "bin/tradeit setup" in res.blocking_reasons[0]


def test_missing_files_block(tmp_path: Path) -> None:
    path = write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    summary = tmp_path / "data" / "broker" / "account" / "paper_account_summary_20261009_163210.csv"
    good = summary.read_text()
    summary.write_text("tag,value\nTotalCashValue,100000\n")
    res = run(tmp_path, path)
    assert not res.ok and "no NetLiquidation" in res.blocking_reasons[0]
    summary.write_text(good)
    path.unlink()
    res = run(tmp_path, path)
    assert not res.ok and "orders file not found" in res.blocking_reasons[0]


def test_selling_down_a_levered_book_passes_with_warnings(tmp_path: Path) -> None:
    path = write_run(tmp_path, levered_book(), [("SPY", -40)], cash=-27_000)
    res = run(tmp_path, path)
    assert res.ok, res.blocking_reasons
    assert res.details["reduce_only"] is True
    assert {w.split(" ")[2] for w in res.warnings} == {"gross", "cash"}
    assert all("only reduce positions" in w for w in res.warnings)


def test_the_runner_step_blocks_on_leverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.execution import kill_switch
    from src.production import daily_runner
    from src.production.runtime import submit_paper_orders

    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(submit_paper_orders, "_project_root", lambda: tmp_path)  # where it reads orders
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "artifacts" / "runs" / "kill_switch.json")

    write_run(tmp_path, levered_book(), [("TLT", 200), ("SPY", -20)], cash=-27_000)
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", dry_run=True)
    assert out["ok"] is False and out["blocked"] is True
    # the engine's target-weight gate passes; the whole-book check is what blocks
    assert out["reasons"] and all(r.startswith("no leverage: ") for r in out["reasons"])
    assert out["risk_details"]["leverage"]["after"]["gross"] == pytest.approx(1.037, abs=1e-4)

    write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", dry_run=True)
    assert out == {"ok": True, "action": "submit_orders", "skipped": True, "reason": "dry_run"}
