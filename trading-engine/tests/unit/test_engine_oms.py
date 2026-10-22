"""The C++ engine's order manager (dv02_engine.oms, engine/src/oms.cpp) against
the Python runner's, input for input: the calendar day by day, the auction at
the times that matter, and thousands of random drift gates and baskets.

The shadow (src/production/engine_shadow.py) compares the two on every run, and
is only worth something if they agree exactly. So every comparison here is
exact: the same shares and sides, and the same dollars to the last bit.
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

oms = pytest.importorskip("dv02_engine.oms")
nyse = pytest.importorskip("dv02_engine.nyse")

from src.common import clock  # noqa: E402
from src.production import engine_shadow  # noqa: E402
from src.production.runtime.build_paper_basket import build_reconciliation  # noqa: E402
from src.runtime.auto_rebalance import decide_rebalance_with_drift  # noqa: E402

NY = clock.NYSE_TZ
MODEL = ["SPY", "QQQ", "TLT", "IEI", "GLD", "SLV", "BIL", "VEA", "VWO", "VNQ", "USO", "UNG"]
OUTSIDE = ["XLE", "XLF", "TQQQ", "IWM", "ABC"]


# ── the calendar ─────────────────────────────────────────────────────────


def days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def test_the_calendar_matches_clock_py_every_day_1990_to_2045() -> None:
    for year in range(1990, 2046):
        assert set(nyse.holidays(year)) == clock._holidays_for_year(year), year
        assert nyse.easter(year) == clock._easter(year)
    for d in days(date(1990, 1, 1), date(2045, 12, 31)):
        assert nyse.is_business_day(d) == clock.is_us_business_day(d), d
        assert nyse.is_early_close(d) == clock.is_early_close(d), d
        py = clock.market_session_for(d)
        en = nyse.session_for(d)
        assert (py is None) == (en is None), d
        if py is not None:
            assert (en[0], en[1]) == (py.open_at, py.close_at), d
        if d.year > 1990:
            assert nyse.previous_business_day(d) == clock.previous_business_day(d), d


TIMES = [time(0, 0), time(9, 24, 59, 999999), time(9, 25), time(9, 29, 59, 999999), time(9, 30), time(9, 30, 0, 1),
         time(12, 44, 59, 999999), time(12, 45), time(12, 59), time(13, 0), time(13, 14, 59), time(13, 15),
         time(15, 30), time(15, 35), time(15, 44, 59, 999999), time(15, 45), time(15, 49, 59, 999999), time(15, 50),
         time(15, 59, 59), time(16, 0), time(16, 14, 59, 999999), time(16, 15), time(17, 5), time(23, 59, 59)]  # fmt: skip


def test_next_open_and_the_last_completed_session_match() -> None:
    for d in days(date(2024, 1, 1), date(2027, 12, 31)):
        for t in TIMES:
            at = datetime.combine(d, t, tzinfo=NY)
            assert nyse.next_open(at) == clock.next_market_open(at), at
            assert nyse.last_completed_session(at) == clock.last_completed_session(at), at


def test_times_in_other_zones_are_read_in_new_york() -> None:
    at = datetime(2026, 10, 21, 19, 35, tzinfo=timezone.utc)  # 15:35 ET
    assert oms.execution_for(["ARCA"], at)["order_type"] == "MOC"
    assert nyse.last_completed_session(datetime(2026, 10, 21, 20, 15, tzinfo=timezone.utc)) == date(2026, 10, 21)


# ── the auction ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("symbols", [["SPY"], ["QQQ"], ["VIXY", "SPY"], ["QQQ", "TLT", "IEI"], []])
def test_the_auction_matches_auction_py(symbols: list[str]) -> None:
    for d in days(date(2026, 1, 1), date(2027, 1, 10)):
        for t in TIMES:
            at = datetime.combine(d, t, tzinfo=NY)
            out = engine_shadow.compare_execution(symbols, at)
            assert out["differences"] == [], (at, out)


def test_moc_cutoffs_by_exchange() -> None:
    minutes = {e: oms.moc_cutoff(e) // 60_000_000 for e in ("NYSE", "ARCA", "NASDAQ", "BATS", "IEX")}
    assert minutes == {"NYSE": 10, "ARCA": 10, "NASDAQ": 5, "BATS": 5, "IEX": 10}


# ── the drift gate ───────────────────────────────────────────────────────


def random_weights(rng: random.Random, names: list[str]) -> dict[str, float]:
    picked = rng.sample(names, rng.randint(0, len(names)))
    return {s: rng.choice([0.0, 0.01, 0.05, 0.049999, 0.05000001]) if rng.random() < 0.2 else rng.random() / 4
            for s in picked}  # fmt: skip


def test_the_drift_gate_matches_on_random_inputs() -> None:
    rng = random.Random(20261021)
    today = date(2026, 10, 21)
    for _ in range(4000):
        kw = dict(
            target_weights=random_weights(rng, MODEL),
            current_weights=random_weights(rng, MODEL + OUTSIDE),
            today=today,
            last_submitted_at=rng.choice([None, today - timedelta(days=rng.randint(-3, 9))]),
            last_basket=rng.choice([None, random_weights(rng, MODEL)]),
            drift_threshold=rng.choice([0.0, 0.02, 0.05, 0.1]),
            composition_threshold=rng.choice([0.0, 0.05, 0.1]),
            cooldown_days=rng.randint(0, 5),
            force=rng.random() < 0.05,
            outside_model=rng.sample(OUTSIDE, rng.randint(0, 2)) if rng.random() < 0.3 else [],
        )
        py = decide_rebalance_with_drift(**kw)
        en = oms.rebalance_decision(**{k: v for k, v in kw.items() if k not in ("target_weights", "current_weights", "today")},
                                    target_weights=kw["target_weights"], current_weights=kw["current_weights"], today=today)  # fmt: skip
        assert (en.rebalance, en.reason, en.composition_changed, en.in_cooldown, en.cooldown_remaining_days) == (
            py.rebalance, py.reason, py.composition_changed, py.in_cooldown, py.cooldown_remaining_days), kw  # fmt: skip
        assert en.max_drift == py.max_drift and dict(en.drift_per_symbol) == dict(py.drift_per_symbol), kw


def test_current_weights_match_the_runner() -> None:
    from src.production import daily_runner

    rng = random.Random(7)
    for _ in range(1000):
        rows = []
        for s in rng.sample(MODEL + OUTSIDE, rng.randint(0, 8)):
            rows.append({"symbol": rng.choice([s, s.lower(), f" {s} "]),
                         "current_shares": rng.choice([0.0, float(rng.randint(-500, 5000)), rng.random() * 100]),
                         "sec_type": rng.choice(["STK", "ETF", "", "FUT", "OPT", None]),
                         "market_price": rng.choice([math.nan, 0.0, rng.uniform(5, 600)])})  # fmt: skip
        positions = pd.DataFrame(rows, columns=["symbol", "current_shares", "sec_type", "market_price"])
        fallback = {s: rng.choice([0.0, rng.uniform(5, 600)]) for s in rng.sample(MODEL + OUTSIDE, 6)}
        nav = rng.uniform(1e4, 3e6)
        py = daily_runner._current_weights(positions, nav, fallback)
        en = oms.current_weights(engine_shadow.holdings(positions), nav, fallback)
        assert dict(en.weights) == py


# ── the basket ───────────────────────────────────────────────────────────


def random_basket(rng: random.Random) -> dict:
    names = rng.sample(MODEL, rng.randint(1, len(MODEL)))
    raw = [rng.random() if rng.random() > 0.15 else 0.0 for _ in names]
    scale = rng.uniform(0.5, 1.0) / (sum(raw) or 1.0)
    latest = pd.DataFrame({
        "date": pd.Timestamp("2026-10-20"),
        "symbol": [rng.choice([s, s.lower()]) for s in names],
        "target_weight": [w * scale for w in raw],
        "close": [rng.uniform(10, 700) for _ in names],
    })  # fmt: skip
    nav = rng.uniform(5e4, 5e6)
    rows = []
    for s in names:
        if rng.random() < 0.6:
            mark = rng.choice([math.nan, rng.uniform(10, 700)])
            qty = rng.choice([float(rng.randint(0, int(nav / 50))), float(rng.randint(-200, 0)), rng.random() * 50 + 0.5])
            rows.append({"symbol": s, "current_shares": qty, "sec_type": rng.choice(["STK", "ETF", ""]),
                         "market_price": mark})  # fmt: skip
    for s in rng.sample(OUTSIDE, rng.randint(0, 3)):
        rows.append({"symbol": s, "current_shares": rng.choice([float(rng.randint(1, 400)), 0.4, -float(rng.randint(1, 50))]),
                     "sec_type": "STK", "market_price": rng.choice([math.nan, rng.uniform(5, 300)])})  # fmt: skip
    if rng.random() < 0.3:
        rows.append({"symbol": "MESZ6", "current_shares": float(rng.choice([-2, 1])), "sec_type": "FUT", "market_price": 6650.0})
    if rng.random() < 0.05:  # a target with no price anywhere: both refuse
        latest.loc[0, "close"] = math.nan
    positions = pd.DataFrame(rows, columns=["symbol", "current_shares", "sec_type", "market_price"])
    if len(positions):
        positions["symbol"] = positions["symbol"].astype(str)
    model = [] if rng.random() < 0.3 else [s for s in MODEL if rng.random() < 0.9]
    whitelist = rng.sample(MODEL, 4) if rng.random() < 0.05 else []
    return {
        "latest": latest, "positions": positions, "nav": nav,
        "cash_buffer": rng.choice([0.0, 0.005, 0.02]),
        "min_trade_dollars": rng.choice([0.0, 100.0, 1000.0, 5000.0]),
        "model": model, "whitelist": whitelist,
        "last_closes": {s: rng.uniform(5, 300) for s in OUTSIDE if rng.random() < 0.5},
        "cash": rng.choice([None, rng.uniform(-0.05, 1.0) * nav, rng.uniform(0.0, 0.1) * nav]),
    }  # fmt: skip


def test_the_basket_matches_build_reconciliation_on_random_books() -> None:
    rng = random.Random(42)
    trimmed = refused = outside = 0
    for case in range(1500):
        c = random_basket(rng)
        en = oms.plan_basket(engine_shadow.targets(c["latest"]), engine_shadow.holdings(c["positions"]), c["nav"],
                             cash_buffer=c["cash_buffer"], min_trade_dollars=c["min_trade_dollars"], model=c["model"],
                             whitelist=c["whitelist"], last_closes=c["last_closes"], cash=c["cash"])  # fmt: skip
        try:
            recon, orders = build_reconciliation(
                c["latest"], c["positions"], c["nav"], cash_buffer=c["cash_buffer"],
                min_trade_dollars=c["min_trade_dollars"], symbol_whitelist=c["whitelist"], model=c["model"],
                last_closes=c["last_closes"], cash=c["cash"],
            )  # fmt: skip
        except Exception:  # noqa: BLE001
            assert not en.ok, (case, "Python refused, the engine didn't")
            refused += 1
            continue
        assert en.ok, (case, en.error)
        out = engine_shadow.compare_basket(recon, orders, en)
        assert out["differences"] == [], (case, out["differences"][:5])
        trimmed += en.trim_keep < 1.0
        outside += bool(en.outside_sell)
    # the cases cover what they should: trims, refusals and holdings outside the model
    assert trimmed > 150 and refused > 25 and outside > 250, (trimmed, refused, outside)


# ── the shadow, as the run calls it ──────────────────────────────────────


def two_etf_basket():
    latest = pd.DataFrame({"date": pd.Timestamp("2026-10-20"), "symbol": ["SPY", "TLT"], "target_weight": [0.6, 0.4],
                           "close": [500.0, 100.0]})  # fmt: skip
    positions = pd.DataFrame([{"symbol": "XLE", "current_shares": 10.0, "sec_type": "STK", "market_price": 90.0}])
    kw = dict(cash_buffer=0.005, min_trade_dollars=1000.0, symbol_whitelist=(), model=("SPY", "TLT"),
              last_closes={}, cash=50_000.0)  # fmt: skip
    recon, orders = build_reconciliation(latest, positions, 100_000.0, **kw)
    return latest, positions, kw, recon, orders


AT = datetime(2026, 10, 21, 19, 35, tzinfo=timezone.utc)  # 15:35 ET


def test_a_matching_basket_is_recorded(tmp_path: Path) -> None:
    latest, positions, kw, recon, orders = two_etf_basket()
    out = engine_shadow.basket(latest, positions, 100_000.0, **kw, recon=recon, orders_only=orders, root=tmp_path, at=AT)
    assert out == {"kind": "basket", "session": "2026-10-21", "match": True, "summary": "same 3 orders"}
    rows = engine_shadow.read_record(tmp_path)
    assert len(rows) == 1 and rows[0]["match"] and rows[0]["python_orders"] == "3"
    detail = json.loads(next((tmp_path / "artifacts" / "engine_shadow" / "2026-10-21").glob("*_basket.json")).read_text())
    assert detail["engine_orders"][0] == ["XLE", "SELL", 10] and detail["execution"]["engine"]["order_type"] == "MOC"


def test_a_difference_is_listed_and_breaks_the_match(tmp_path: Path) -> None:
    latest, positions, kw, recon, orders = two_etf_basket()
    orders.loc[orders["symbol"] == "SPY", "delta_shares"] += 1  # Python "would send" one more share
    recon.loc[recon["symbol"] == "SPY", "delta_shares"] += 1
    out = engine_shadow.basket(latest, positions, 100_000.0, **kw, recon=recon, orders_only=orders, root=tmp_path, at=AT)
    assert out["match"] is False and "SPY" in out["summary"]
    detail = json.loads(next((tmp_path / "artifacts" / "engine_shadow" / "2026-10-21").glob("*_basket.json")).read_text())
    whats = [d["what"] for d in detail["differences"]]
    assert "line 1 (SPY) delta_shares" in whats and "orders, in sending order" in whats


def test_a_missing_engine_is_recorded_not_raised(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "dv02_engine", None)
    monkeypatch.setitem(sys.modules, "dv02_engine.oms", None)
    latest, positions, kw, recon, orders = two_etf_basket()
    out = engine_shadow.basket(latest, positions, 100_000.0, **kw, recon=recon, orders_only=orders, root=tmp_path, at=AT)
    assert out["match"] is False and "isn't installed" in out["summary"]


def test_the_streak_counts_matching_sessions_back_from_the_latest(tmp_path: Path) -> None:
    latest, positions, kw, recon, orders = two_etf_basket()
    for day in (19, 20, 21):
        at = datetime(2026, 10, day, 19, 35, tzinfo=timezone.utc)
        engine_shadow.basket(latest, positions, 100_000.0, **kw, recon=recon, orders_only=orders, root=tmp_path, at=at)
    bad = recon.copy()
    bad.loc[0, "target_shares"] += 1
    engine_shadow.basket(latest, positions, 100_000.0, **kw, recon=bad, orders_only=orders, root=tmp_path,
                         at=datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc))  # fmt: skip
    s = engine_shadow.status(tmp_path)
    assert s["streak"] == 2 and s["needed"] == engine_shadow.STREAK_NEEDED and not s["ready"]
    assert [d["session"] for d in s["sessions"]] == ["2026-10-21", "2026-10-20", "2026-10-19"]
    assert s["last_difference"]["session"] == "2026-10-19"


def test_whatif_builds_both_baskets_from_the_run_s_files(tmp_path: Path) -> None:
    targets_dir = tmp_path / "data" / "market" / "cleaned" / "targets"
    prices_dir = tmp_path / "data" / "market" / "cleaned" / "prices"
    targets_dir.mkdir(parents=True)
    prices_dir.mkdir(parents=True)
    pd.DataFrame({"rebalance_date": pd.Timestamp("2026-10-20"), "symbol": ["SPY", "TLT", "TQQQ"],
                  "target_weight": [0.6, 0.3, 0.1], "reference_price": [500.0, 100.0, 50.0]}).to_parquet(
        targets_dir / "etf_targets_monthly.parquet")  # fmt: skip
    pd.DataFrame({"date": pd.Timestamp("2026-10-20"), "symbol": ["XLE"], "close": [88.0]}).to_parquet(
        prices_dir / "etf_prices_master.parquet")  # fmt: skip
    positions = pd.DataFrame([{"symbol": "SPY", "current_shares": 50.0, "sec_type": "STK", "market_price": 505.0},
                              {"symbol": "XLE", "current_shares": 12.0, "sec_type": "STK", "market_price": math.nan}])
    out = engine_shadow.whatif(positions, 250_000.0, root=tmp_path, at=AT)
    assert out["match"] is True, out


def test_the_runner_shadows_its_drift_gate(tmp_path: Path, monkeypatch) -> None:
    from src.production import daily_runner

    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 10, 13))
    rb = {"cadence": "drift", "drift_threshold": 0.05, "composition_threshold": 0.05, "cooldown_days": 3,
          "state_path": "data/broker/state/last_rebalance.json"}  # fmt: skip
    latest = pd.DataFrame({"rebalance_date": date(2026, 10, 12), "symbol": ["SPY", "TLT"], "target_weight": [0.5, 0.5],
                           "close": [100.0, 100.0]})  # fmt: skip
    positions = pd.DataFrame([{"symbol": "SPY", "current_shares": 6000.0, "sec_type": "STK", "market_price": 100.0},
                              {"symbol": "TLT", "current_shares": 4000.0, "sec_type": "STK", "market_price": 100.0}])
    monkeypatch.setattr("src.production.generate_targets.model_universe", lambda path=None: ("SPY", "TLT"))
    decided, diag = daily_runner._needs_rebalance_drift("paper", rb, targets_df=latest, positions=positions,
                                                        nav=1_000_000.0, source="ibkr")  # fmt: skip
    assert decided is True  # no last basket: a cold start
    assert diag["engine"]["match"] is True, diag["engine"]
    assert engine_shadow.read_record(tmp_path)[0]["kind"] == "drift"


# ── the reconcile ────────────────────────────────────────────────────────


def random_reconcile(rng: random.Random) -> dict:
    names = rng.sample(MODEL + OUTSIDE, rng.randint(1, 10))
    book_positions = {s: float(rng.randint(-50, 3000)) for s in rng.sample(names, rng.randint(0, len(names)))}
    unit_values = {s: rng.uniform(5, 700) for s in names if rng.random() < 0.6}
    orders, fills = [], []
    for k, s in enumerate(rng.sample(names, rng.randint(0, len(names)))):
        qty = float(rng.choice([1, -1]) * rng.randint(1, 400))
        perm = str(355965660 + k) if rng.random() < 0.7 else None
        oid = str(4 + k) if rng.random() < 0.8 else None
        status = rng.choice(["PreSubmitted", "PendingSubmit", "Submitted", "Filled", "ERROR", "Cancelled", ""])
        orders.append({"symbol": s, "quantity": qty, "status": status, "order_id": oid, "perm_id": perm,
                       "price": rng.choice([None, rng.uniform(5, 700)])})  # fmt: skip
        if rng.random() < 0.5:  # IBKR reports part, all or more than the order (a stray fill)
            for _ in range(rng.randint(1, 3)):
                fills.append({"perm_id": perm if rng.random() < 0.8 else "999", "order_id": oid, "symbol": s,
                              "quantity": qty * rng.choice([0.25, 0.5, 1.0])})  # fmt: skip
    from src.runtime.end_of_day_reconcile import expected_positions

    book = {"positions": book_positions, "unit_values": unit_values, "orders": orders}
    expected_now, _ = expected_positions(book, fills)
    agree = rng.random() < 0.4  # IBKR holds exactly the expected book, or differs here and there
    rows = []
    for s, q in expected_now.items():
        have = q if agree or rng.random() < 0.7 else q + rng.choice([-3.0, -1.0, 0.5, 2.0, -q])
        rows.append({"symbol": rng.choice([s, s.lower()]), "current_shares": have, "sec_type": "STK", "multiplier": math.nan,
                     "market_price": rng.choice([math.nan, rng.uniform(5, 700)])})  # fmt: skip
    if rng.random() < 0.3:
        rows.append({"symbol": "MESZ6", "current_shares": -2.0, "sec_type": "FUT", "multiplier": rng.choice([5.0, math.nan]),
                     "market_price": 6650.0})  # fmt: skip
    return {"book": book, "positions": pd.DataFrame(rows), "fills": fills, "threshold": rng.choice([0.0, 100.0, 1000.0])}


def test_the_reconcile_matches_end_of_day_reconcile_on_random_books() -> None:
    from src.runtime import end_of_day_reconcile as eod

    rng = random.Random(1021)
    material = breaks = 0
    for case in range(2000):
        c = random_reconcile(rng)
        actual, marks = eod._held(c["positions"])
        expected, orders = eod.expected_positions(c["book"], c["fills"])
        order_prices = {o["symbol"]: o["price"] for o in c["book"]["orders"] if o.get("price")}
        found = eod.position_breaks(expected, actual, {**c["book"]["unit_values"], **order_prices, **marks})
        is_material = any(b["notional_diff_usd"] is None or b["notional_diff_usd"] >= c["threshold"] for b in found)
        out = engine_shadow.compare_reconcile(book=c["book"], positions=c["positions"], fills=c["fills"],
                                              threshold=c["threshold"], expected=expected, orders=orders, breaks=found,
                                              material=is_material)  # fmt: skip
        assert out["differences"] == [], (case, out["differences"][:5])
        material += is_material
        breaks += bool(found)
    assert material > 300 and breaks > 500 and 2000 - breaks > 300, (material, breaks)


def test_the_reconcile_records_its_comparison(tmp_path: Path) -> None:
    from src.runtime import end_of_day_reconcile as eod

    book = {"positions": {"SPY": 100.0}, "unit_values": {"SPY": 500.0},
            "orders": [{"symbol": "SPY", "quantity": 10.0, "status": "PreSubmitted", "order_id": "4", "perm_id": "355"}]}  # fmt: skip
    positions = pd.DataFrame([{"symbol": "SPY", "current_shares": 109.0, "sec_type": "STK", "market_price": 501.0}])
    expected, orders = eod.expected_positions(book, [])
    found = eod.position_breaks(expected, {"SPY": 109.0}, {"SPY": 501.0})
    out = engine_shadow.reconcile(root=tmp_path, at=AT, book=book, positions=positions, fills=[], threshold=100.0,
                                  expected=expected, orders=orders, breaks=found, material=True)  # fmt: skip
    assert out["match"] is True and out["summary"] == "same 1 break, material"


# ── order states ─────────────────────────────────────────────────────────

STATUSES = ["", "PendingSubmit", "ApiPending", "PreSubmitted", "Submitted", "ApiUpdate", "PendingCancel", "Cancelled",
            "ApiCancelled", "Filled", "Inactive", "ERROR"]  # fmt: skip


def test_order_states_match_the_order_path_on_random_status_logs() -> None:
    """Fake ib_async trades, recorded exactly as submit_paper_orders records them,
    then each row's status log replayed by the engine."""
    from types import SimpleNamespace

    from src.broker.ibkr.orders import acknowledged_after, trade_log, trade_status

    rng = random.Random(22)
    sent = datetime(2026, 10, 22, 19, 35, 1, tzinfo=timezone.utc)
    rows, acknowledged = [], 0
    for _ in range(3000):
        t, entries = sent, []
        for k in range(rng.randint(1, 6)):
            t += timedelta(seconds=rng.choice([0.0, 0.004, 0.31, 2.5, 12.04, 29.99]))
            status = "PendingSubmit" if k == 0 else rng.choice(STATUSES[:-1])
            code, message = rng.choice([(0, ""), (0, ""), (399, "Order Message: Warning"), (201, "Order rejected")])
            entries.append(SimpleNamespace(time=t, status=status, errorCode=code, message=message))
        trade = SimpleNamespace(log=entries, orderStatus=SimpleNamespace(status=entries[-1].status))
        ack = acknowledged_after(trade)
        acknowledged += ack is not None
        rows.append({"symbol": "SPY", "action": "BUY", "quantity": 1, "status": trade_status(trade),
                     "acknowledged_s": "" if ack is None else str(ack), "status_log": json.dumps(trade_log(trade))})  # fmt: skip
    rows += [{"symbol": "GLD", "action": "SELL", "quantity": 3, "status": "ERROR", "error": "not qualified"}]
    out = engine_shadow.compare_orders(rows)
    assert out["differences"] == [], out["differences"][:5]
    assert out["same"] == "same 3001 order states" and 500 < acknowledged < 2900
