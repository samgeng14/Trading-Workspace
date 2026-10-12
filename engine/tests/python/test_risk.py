"""The Python face of the C++ leverage check: the bindings carry the same rules."""

import pytest

risk = pytest.importorskip("dv02_engine.risk")


def etf(symbol, qty, px):
    return risk.Position(symbol, qty, px, asset_class=risk.AssetClass.ETF)


def test_levered_rebalance_blocks():
    book = risk.Book(1_000_000, -25_000, [etf("SPY", 1500, 500), etf("DBC", 13_750, 20)])
    r = risk.check(book, [risk.Order("TLT", 100, 100)])
    assert not r.ok
    rules = {b.rule for b in r.breaches if b.blocking}
    assert {"gross", "cash"} <= rules
    assert r.after.gross == pytest.approx(1.035)


def test_reduce_only_passes_with_warning():
    book = risk.Book(1_000_000, -25_000, [etf("SPY", 1500, 500), etf("DBC", 13_750, 20)])
    r = risk.check(book, [risk.Order("DBC", -500, 20)], risk.Policy(max_single_name=1.0))
    assert r.ok and r.reduce_only
    assert [b.rule for b in r.breaches] == ["gross", "cash"]
    assert not any(b.blocking for b in r.breaches)


def test_futures_at_notional():
    book = risk.Book(1_000_000, 1_000_000)
    es = risk.Order("ESZ6", 4, 6650, 50, risk.asset_class_from_ibkr("FUT"))
    r = risk.check(book, [es], risk.Policy(max_single_name=2.0))
    assert not r.ok
    assert [b.rule for b in r.breaches] == ["gross"]
    assert r.after.gross == pytest.approx(1.33)
    assert r.after.cash == 1_000_000  # no cash moves for futures


def test_dicts_are_json_ready():
    import json

    r = risk.check(risk.Book(1_000_000, 500_000, [etf("SPY", 1000, 500)]), [])
    doc = risk.result_dict(r)
    json.dumps(doc)
    assert doc["after"]["largest_name"] == "SPY"
    assert risk.policy_dict(risk.Policy())["max_gross"] == 1.0


def test_unpriced_blocks():
    r = risk.check(risk.Book(1_000_000, 0, [etf("XLE", 100, float("nan"))]), [])
    assert not r.ok
    assert "XLE" in r.breaches[0].message
