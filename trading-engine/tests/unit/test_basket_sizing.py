"""The basket is sized at today's prices and never spends more than the cash.

On 2026-10-20 the VM's basket sized every trade at the targets' close (the day
before), while the account and the no-leverage check value holdings at IBKR's
mark. The market was up, so the held names looked under-invested and the basket
planned a net buy of $10,100 with $7,429 in cash: the check refused it (gross
100.3%, cash -$2,682). Held names are now sized at the mark, and buys are cut
to the cash above the 0.5% buffer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production.runtime.build_paper_basket import (  # noqa: E402
    account_cash,
    build_reconciliation,
    trimmed_buys,
)

NAV = 1_000_000.0
NAMES = ("SPY", "QQQ", "TLT", "GLD", "BIL")
BUFFER = 0.005


def targets(weights: dict[str, float], close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp("2026-10-19"), "symbol": s, "target_weight": w, "close": close} for s, w in weights.items()]
    )


def held(shares: dict[str, float], mark: float | None) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": s, "current_shares": q, "sec_type": "STK", "market_price": mark} for s, q in shares.items()],
        columns=["symbol", "current_shares", "sec_type", "market_price"],
    )


def spend(orders: pd.DataFrame) -> float:
    """What the orders cost net of sells, commissions included, at the price they were sized at."""
    return float((orders["delta_shares"] * orders["price"]).sum() + orders["estimated_commission"].sum())


def test_held_names_are_sized_at_ibkrs_mark_and_new_ones_at_the_close() -> None:
    recon, _ = build_reconciliation(
        targets({"SPY": 0.5, "TLT": 0.495}), held({"SPY": 10}, 102.0), NAV, cash_buffer=BUFFER
    )
    row = recon.set_index("symbol")
    assert row.at["SPY", "price"] == 102.0 and row.at["SPY", "close"] == 100.0
    assert row.at["SPY", "target_shares"] == int(0.5 * NAV * (1 - BUFFER) // 102.0)
    assert row.at["TLT", "price"] == 100.0  # not held: the targets' close
    assert row.at["SPY", "estimated_trade_dollars"] == pytest.approx(row.at["SPY", "abs_delta_shares"] * 102.0)


def test_the_day_the_market_rose_the_basket_fits_the_mandate(tmp_path) -> None:
    pytest.importorskip("dv02_engine.risk")
    from tests.unit.test_leverage import run, write_book

    # five names at 19.9% each, marked 1.5% above the targets' close, 0.53% in cash
    weights = {s: 0.199 for s in NAMES}
    shares = {s: 1_960 for s in NAMES}
    cash = NAV - 5 * 1_960 * 101.5

    def check(recon: pd.DataFrame, orders: pd.DataFrame):
        book = [(s, q, 101.5) for s, q in shares.items()]
        path = write_book(tmp_path, book, [(r.symbol, int(r.delta_shares), float(r.price)) for r in orders.itertuples()],
                          cash=cash)  # fmt: skip
        return run(tmp_path, path)

    # as it was: sized at the targets' close, every name looked $1,900 short and was bought
    recon, orders = build_reconciliation(targets(weights), held(shares, None), NAV, cash_buffer=BUFFER)
    assert set(orders["side"]) == {"BUY"}
    orders = orders.assign(price=100.0)
    res = check(recon, orders)
    assert not res.ok and any("margin loan" in r for r in res.blocking_reasons)

    # now: at the mark every name is $1,015 over its target, so the basket sells a little of each
    recon, orders = build_reconciliation(targets(weights), held(shares, 101.5), NAV, cash_buffer=BUFFER, cash=cash)
    assert len(orders) == 5 and (orders["side"] == "SELL").all() and (orders["abs_delta_shares"] == 10).all()
    res = check(recon, orders)
    assert res.ok, res.blocking_reasons
    assert res.details["after"]["gross"] <= 1 - BUFFER and res.details["after"]["cash"] >= BUFFER * NAV


def test_buys_are_cut_to_the_cash_above_the_buffer() -> None:
    # TLT and GLD are 5% under target, the rest on it, and cash is only 4% of NAV
    weights = {s: 0.199 for s in NAMES}
    on_target = int(0.199 * NAV * (1 - BUFFER) // 100.0)
    shares = {s: on_target for s in NAMES} | {"TLT": on_target - 500, "GLD": on_target - 500}
    cash = 40_000.0
    recon, orders = build_reconciliation(
        targets(weights), held(shares, 100.0), NAV, cash_buffer=BUFFER, min_trade_dollars=1_000.0, cash=cash
    )
    # uncut, the two buys would spend $100,000; the cash above the $5,000 buffer is $35,000
    assert spend(orders) <= cash - BUFFER * NAV
    assert spend(orders) > cash - BUFFER * NAV - 300  # cut no more than the rounding needs
    assert set(orders["symbol"]) == {"TLT", "GLD"} and (orders["side"] == "BUY").all()
    cut = trimmed_buys(recon)
    assert cut is not None and cut["symbols"] == ["GLD", "TLT"]
    assert cut["dollars"] == pytest.approx(100_000 - 34_800, abs=300)
    assert "cut by $" in cut["note"]

    # without the cash the basket isn't trimmed (the no-leverage check still guards it)
    _, uncut = build_reconciliation(targets(weights), held(shares, 100.0), NAV, cash_buffer=BUFFER)
    assert spend(uncut) == pytest.approx(100_000 + 10.0)


def test_sells_are_never_cut_and_fund_the_buys() -> None:
    weights = {"SPY": 0.5, "TLT": 0.495}
    cash = -200_000.0  # a margin loan
    recon, orders = build_reconciliation(
        targets(weights), held({"SPY": 7_000, "XLE": 1_000, "TLT": 0}, 100.0), NAV, cash_buffer=BUFFER, cash=cash
    )
    sells = orders.loc[orders["side"] == "SELL"].set_index("symbol")
    assert sells.at["SPY", "abs_delta_shares"] == 7_000 - int(0.5 * NAV * (1 - BUFFER) // 100.0)
    assert sells.at["XLE", "abs_delta_shares"] == 1_000  # outside the model: sold whole
    # the $302,500 of sells repay the loan first; what is left above the buffer buys TLT
    tlt = recon.set_index("symbol").loc["TLT"]
    assert tlt["trimmed_shares"] > 0 and 0 < tlt["delta_shares"] < int(0.495 * NAV * (1 - BUFFER) // 100.0)
    assert cash - spend(orders) >= BUFFER * NAV
    assert cash - spend(orders) < BUFFER * NAV + 200


def test_a_buy_cut_under_the_minimum_is_dropped() -> None:
    weights = {s: 0.199 for s in NAMES}
    on_target = int(0.199 * NAV * (1 - BUFFER) // 100.0)
    shares = {s: on_target for s in NAMES} | {"TLT": on_target - 15}  # a $1,500 buy
    recon, orders = build_reconciliation(
        targets(weights), held(shares, 100.0), NAV, cash_buffer=BUFFER, min_trade_dollars=1_000.0,
        cash=BUFFER * NAV + 900.0,
    )  # fmt: skip
    assert orders.empty
    assert recon.set_index("symbol").at["TLT", "trimmed_shares"] > 0


def test_the_account_summary_gives_the_cash() -> None:
    summary = pd.DataFrame([{"tag": "NetLiquidation", "value": "1046853.62"}, {"tag": "TotalCashValue", "value": "7429.32"}])
    assert account_cash(summary) == pytest.approx(7_429.32)
    assert account_cash(summary.iloc[:1]) is None
    assert account_cash(pd.DataFrame(columns=["tag", "value"])) is None
