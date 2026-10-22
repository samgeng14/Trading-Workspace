"""Signals on total-return closes: IBKR's dividend-adjusted bars where they
exist, the master's price return past them, and price for any symbol whose
adjusted bars don't agree with the master on splits."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.cleaning import total_return as tr  # noqa: E402
from src.production import generate_targets as gt  # noqa: E402
from src.production.pipeline import append_total_return as atr  # noqa: E402

DATES = pd.bdate_range("2025-01-02", "2025-12-31")


def bill_fund(dates=DATES, price=91.50, monthly=0.35):
    """A T-bill fund: the price drifts up a month's income, then drops it on the
    first business day of each month (the ex-date). Returns (master rows, IBKR
    adjusted closes as of the last day)."""
    month = dates.to_period("M")
    ex = np.r_[False, month[1:] != month[:-1]]
    day_in_month = pd.Series(1, index=dates).groupby(month).cumcount().to_numpy()
    days_in_month = pd.Series(1, index=dates).groupby(month).transform("size").to_numpy()
    close = price + monthly * day_in_month / days_in_month  # accrues, then pays out
    adj = close.copy()
    for i in np.flatnonzero(ex)[::-1]:  # each dividend scales everything before its ex-date
        dividend = close[i - 1] + monthly / days_in_month[i - 1] - close[i]
        adj[:i] *= 1 - dividend / close[i - 1]
    master = pd.DataFrame({"date": dates, "symbol": "BIL", "close": close.round(2)})
    adjusted = pd.DataFrame({"date": dates, "symbol": "BIL", "adj_close": adj.round(2)})
    return master, adjusted


def stock(symbol, dates=DATES, start=100.0, step=0.001):
    close = start * (1 + step) ** np.arange(len(dates))
    return pd.DataFrame({"date": dates, "symbol": symbol, "close": close.round(2)})


# ── signal_closes ─────────────────────────────────────────────────────────


def test_a_bill_fund_earns_its_income_in_total_return_and_not_in_price():
    master, adjusted = bill_fund()
    sig = tr.signal_closes(master, adjusted)
    assert sig.basis == "total_return" and sig.on_price == {}
    price_ret = master["close"].iloc[-1] / master["close"].iloc[0] - 1
    total_ret = sig.closes.iloc[-1] / sig.closes.iloc[0] - 1
    assert abs(price_ret) < 0.004  # the price barely moves over the year
    assert total_ret == pytest.approx(0.044, abs=0.004)  # ~12 x 0.35 on 91.5
    # on a day both have, the signal is IBKR's adjusted close itself
    assert sig.closes.to_numpy() == pytest.approx(adjusted["adj_close"].to_numpy(), abs=1e-9)
    # and the master, the order prices, is untouched
    assert master["close"].equals(bill_fund()[0]["close"])


def test_days_after_the_last_adjusted_close_follow_the_price_return():
    master, adjusted = bill_fund()
    fetched = adjusted[adjusted["date"] <= "2025-11-28"]  # refreshed a month ago
    sig = tr.signal_closes(master, fetched)
    last = master.index[master["date"] == "2025-11-28"][0]
    factor = fetched["adj_close"].iloc[-1] / master["close"].iloc[last]
    tail = master.index > last
    assert sig.closes[tail].to_numpy() == pytest.approx(master.loc[tail, "close"].to_numpy() * factor)


def test_days_before_the_first_adjusted_close_follow_it_backwards():
    master, adjusted = bill_fund()
    sig = tr.signal_closes(master, adjusted[adjusted["date"] >= "2025-03-03"])
    first = master.index[master["date"] == "2025-03-03"][0]
    factor = sig.closes[first] / master["close"][first]
    head = master.index < first
    assert sig.closes[head].to_numpy() == pytest.approx(master.loc[head, "close"].to_numpy() * factor)


def test_no_adjusted_file_is_price_for_everything():
    master = pd.concat([bill_fund()[0], stock("SPY")], ignore_index=True)
    sig = tr.signal_closes(master, None)
    assert sig.basis == "price" and sig.closes.equals(master["close"].astype(float))
    assert sig.note() == "on price, no adjusted closes yet: BIL, SPY"


def test_a_symbol_without_adjusted_closes_stays_on_price_and_is_named():
    bil, bil_adj = bill_fund()
    master = pd.concat([bil, stock("VIXY")], ignore_index=True)
    sig = tr.signal_closes(master, bil_adj)
    assert sig.basis == "total_return" and sig.on_price == {"VIXY": "no adjusted closes"}
    vixy = master["symbol"] == "VIXY"
    assert sig.closes[vixy].equals(master.loc[vixy, "close"].astype(float))


def test_adjusted_bars_on_another_split_basis_stay_on_price():
    master = stock("UNG", start=12.0)
    # IBKR's adjusted series after a 1:4 reverse split the master hasn't been refetched for
    adjusted = master.rename(columns={"close": "adj_close"}).assign(adj_close=lambda d: d["adj_close"] * 4)
    sig = tr.signal_closes(master, adjusted)
    assert "a different split basis" in sig.on_price["UNG"]
    assert sig.closes.equals(master["close"].astype(float))


def test_a_ratio_that_falls_is_a_split_the_two_disagree_on():
    master = stock("SPY")
    adjusted = master.rename(columns={"close": "adj_close"})
    adjusted.loc[adjusted.index >= 100, "adj_close"] *= 0.5  # a 2:1 split only one side applied
    why = tr.signal_closes(master, adjusted).on_price["SPY"]
    assert "falls 50.0%" in why and str(DATES[100].date()) in why


def test_rounding_to_the_cent_is_not_a_split():
    master = stock("VIXY", start=11.03, step=-0.002)  # a low price: a cent is ~0.1%
    noisy = master.rename(columns={"close": "adj_close"})
    noisy["adj_close"] = (noisy["adj_close"] + np.where(np.arange(len(noisy)) % 2, 0.01, -0.01)).round(2)
    assert tr.signal_closes(master, noisy).on_price == {}


def test_bad_rows_in_the_adjusted_file_are_dropped():
    adj = pd.DataFrame({"Date": ["2025-01-02", "2025-01-03", "2025-01-03"], "Symbol": ["bil"] * 3,
                        "Close": [91.0, -1.0, 91.1]})  # fmt: skip
    out = tr.normalize_adjusted(adj)
    assert list(out["symbol"]) == ["BIL", "BIL"] and list(out["adj_close"]) == [91.0, 91.1]


# ── IBKR's bad days: found, and repaired in the signals ───────────────────


def bond_fund(symbol="TLT", start=95.0, factor=0.87457):
    """A bond fund between payouts: the master's closes, and IBKR's adjusted
    closes at a constant factor (no ex-date in the window)."""
    master = stock(symbol, start=start, step=0.0002)
    adjusted = master.rename(columns={"close": "adj_close"}).assign(adj_close=lambda d: d["adj_close"] * factor)
    return master, adjusted


def factor_of(master, adjusted):
    return adjusted["adj_close"].to_numpy() / master["close"].to_numpy()


def daily(series: pd.Series) -> np.ndarray:
    return series.to_numpy() / np.r_[np.nan, series.to_numpy()[:-1]] - 1


def test_a_one_day_dip_in_the_factor_is_a_blip_and_the_signal_follows_the_price():
    master, adjusted = bond_fund()
    adjusted.loc[150, "adj_close"] *= 0.87241 / 0.87457  # TLT on 2023-08-09: 0.25% lower for one day
    sig = tr.signal_closes(master, adjusted)
    assert [(b.symbol, b.date, b.kind, b.sessions) for b in sig.repaired] == [("TLT", DATES[150], "blip", 1)]
    assert sig.repaired[0].size == pytest.approx(-0.00247, abs=2e-5)
    assert sig.on_price == {}
    # the signal's returns are the price's: no dip and no rebound
    assert daily(sig.closes)[1:] == pytest.approx(daily(master["close"])[1:], abs=1e-12)
    assert "TLT " + str(DATES[150].date()) + ": IBKR's adjustment dips 0.25% for a session, then returns" in sig.note()


def test_a_two_day_jump_is_a_blip_too():
    master, adjusted = bond_fund("VNQ", start=83.0)
    adjusted.loc[[60, 61], "adj_close"] *= 1.00085  # VNQ on 2017-03-20, held a day longer
    found = tr.find_bad_days(master, adjusted)
    assert [(b.date, b.kind, b.sessions) for b in found] == [(DATES[60], "blip", 2)]
    assert found[0].describe().endswith("jumps 0.09% for 2 sessions, then returns")


def test_a_one_day_dip_larger_than_a_split_check_is_repaired_not_put_on_price():
    master, adjusted = bond_fund()
    adjusted.loc[100, "adj_close"] *= 0.98  # a bad bar: 2% for a day, then back
    sig = tr.signal_closes(master, adjusted)
    assert sig.on_price == {} and [b.date for b in sig.repaired] == [DATES[100]]


def test_payouts_and_a_cents_rounding_are_not_bad_days():
    master, adjusted = bill_fund()  # twelve ex-dates: the factor steps up and stays
    assert tr.find_bad_days(master, adjusted) == []
    low = stock("VIXY", start=11.03, step=-0.002)  # a cent is ~0.1% at this price
    noisy = low.rename(columns={"close": "adj_close"})
    noisy["adj_close"] = (noisy["adj_close"] + np.where(np.arange(len(noisy)) % 2, 0.01, -0.01)).round(2)
    assert tr.find_bad_days(low, noisy) == []


def without_payout(master, adjusted, i):
    """IBKR's adjusted closes as if the payout going ex on row ``i`` were missing:
    history before it isn't scaled down."""
    f = factor_of(master, adjusted)
    out = adjusted.copy()
    out.loc[: i - 1, "adj_close"] *= f[i] / f[i - 1]
    return out


def test_a_bill_fund_s_missed_payout_is_found_and_put_back():
    master, adjusted = bill_fund()
    ex = master.index[master["date"] == "2025-12-01"][0]  # as BIL on 2024-12-19: a payout IBKR missed
    missing = without_payout(master, adjusted, ex)
    sig = tr.signal_closes(master, missing)
    assert [(b.symbol, b.date, b.kind) for b in sig.repaired] == [("BIL", DATES[ex], "missed_distribution")]
    assert sig.repaired[0].size == pytest.approx(master["close"][ex] / master["close"][ex - 1] - 1)
    assert sig.repaired[0].describe().endswith("the price falls 0.36% and IBKR's adjustment has no payout")
    # that day's total return is 0 instead of minus the payout, so the year keeps its income
    assert daily(sig.closes)[ex] == pytest.approx(0.0, abs=1e-12)
    true = tr.signal_closes(master, adjusted).closes
    year = lambda c: c.iloc[-1] / c.iloc[0] - 1  # noqa: E731
    assert year(sig.closes) == pytest.approx(year(true), abs=0.0003)
    assert year(missing["adj_close"]) < year(true) - 0.003  # IBKR's own series loses it


def test_a_payout_after_the_last_refresh_is_put_back_until_the_next():
    master, adjusted = bill_fund()
    ex = master.index[master["date"] == "2025-12-01"][0]
    before = adjusted[adjusted["date"] < "2025-12-01"]  # refreshed the session before the ex-date
    sig = tr.signal_closes(master, before)
    assert [(b.date, b.kind) for b in sig.repaired] == [(DATES[ex], "missed_distribution")]
    assert daily(sig.closes)[ex] == pytest.approx(0.0, abs=1e-12)


def test_the_first_adjusted_day_is_out_of_reach_and_not_a_missed_payout():
    master, adjusted = bill_fund()
    first = adjusted[adjusted["date"] >= "2025-03-03"]  # 2025-03-03 is an ex-date
    assert tr.find_bad_days(master, first) == []


def test_only_a_cash_like_fund_s_fall_counts_as_a_missed_payout():
    master, adjusted = bond_fund("SHY", start=82.0)
    master.loc[120, "close"] *= 0.995  # SHY on a rate day: 0.5% down, no payout
    adjusted.loc[120, "adj_close"] = master.loc[120, "close"] * 0.87457
    assert tr.find_bad_days(master, adjusted) == []
    bil = master.assign(symbol="BIL")
    assert [b.kind for b in tr.find_bad_days(bil, adjusted.assign(symbol="BIL"))] == ["missed_distribution"]


# ── refresh_adjusted ──────────────────────────────────────────────────────


def _fetcher(series: dict[str, pd.DataFrame], fail: set[str] = frozenset(), short_only: set[str] = frozenset()):
    calls: list[tuple[str, str]] = []

    def fetch(symbol, duration):
        calls.append((symbol, duration))
        if symbol in fail:
            raise RuntimeError("no data")
        if symbol in short_only and duration != "2 Y":
            return pd.DataFrame()
        return series[symbol].rename(columns={"adj_close": "close"})[["date", "close"]]

    return fetch, calls


def test_a_refresh_stores_each_symbol_whole_and_drops_a_session_still_open(tmp_path):
    _, bil_adj = bill_fund()
    path = tmp_path / "adj.parquet"
    fetch, _ = _fetcher({"BIL": bil_adj})
    out = atr.refresh_adjusted(fetch, ["bil"], through=date(2025, 12, 30), path=path)
    assert out["ok"] is True and out["refreshed"] == ["BIL"] and out["errors"] == {}
    stored = atr.load_adjusted(path)
    assert stored["date"].max() == pd.Timestamp("2025-12-30")  # the 31st was still trading
    assert len(stored) == len(DATES) - 1


def test_a_symbol_that_fails_keeps_its_previous_series(tmp_path):
    _, bil_adj = bill_fund()
    spy = stock("SPY").rename(columns={"close": "adj_close"})
    path = tmp_path / "adj.parquet"
    atr.refresh_adjusted(_fetcher({"BIL": bil_adj, "SPY": spy})[0], ["BIL", "SPY"], through=date(2025, 12, 31), path=path)
    newer = spy.assign(adj_close=spy["adj_close"] * 0.99)  # a dividend rescaled SPY's history
    fetch, _ = _fetcher({"SPY": newer}, fail={"BIL"})
    out = atr.refresh_adjusted(fetch, ["BIL", "SPY"], through=date(2025, 12, 31), path=path)
    assert out["ok"] is False and "no data" in out["errors"]["BIL"] and out["refreshed"] == ["SPY"]
    stored = atr.load_adjusted(path)
    assert stored.loc[stored["symbol"] == "BIL", "adj_close"].to_numpy() == pytest.approx(bil_adj["adj_close"].to_numpy())
    assert stored.loc[stored["symbol"] == "SPY", "adj_close"].to_numpy() == pytest.approx(newer["adj_close"].to_numpy())


def test_shorter_durations_are_tried_when_ibkr_returns_nothing(tmp_path):
    _, bil_adj = bill_fund()
    fetch, calls = _fetcher({"BIL": bil_adj}, short_only={"BIL"})
    out = atr.refresh_adjusted(fetch, ["BIL"], through=date(2025, 12, 31), path=tmp_path / "a.parquet")
    assert out["ok"] is True and [d for _, d in calls] == ["10 Y", "5 Y", "2 Y"]


def test_a_refresh_reports_the_bad_days_and_those_inside_the_lookback(tmp_path, caplog):
    master, adjusted = bond_fund()
    adjusted.loc[5, "adj_close"] *= 0.9975  # long before the lookback
    adjusted.loc[240, "adj_close"] *= 0.9975  # inside it
    path = tmp_path / "etf_total_return_closes.parquet"
    master.to_parquet(tmp_path / atr.MASTER_FILE, index=False)
    with caplog.at_level("INFO"):
        out = atr.refresh_adjusted(_fetcher({"TLT": adjusted})[0], ["TLT"], through=date(2025, 12, 31), path=path)
    assert out["ok"] is True
    assert [line[:14] for line in out["bad_days"]] == [f"TLT {DATES[5].date()}", f"TLT {DATES[240].date()}"]
    assert [line[:14] for line in out["recent"]] == [f"TLT {DATES[240].date()}"]
    assert "inside the signals' lookback: TLT " + str(DATES[240].date()) in caplog.text
    assert "1 older bad day in IBKR's series" in caplog.text
    assert atr.check_stored(path) == {"bad_days": out["bad_days"], "recent": out["recent"]}


def test_with_no_master_beside_it_the_refresh_reports_no_bad_days(tmp_path):
    _, bil_adj = bill_fund()
    out = atr.refresh_adjusted(_fetcher({"BIL": bil_adj})[0], ["BIL"], through=date(2025, 12, 31),
                               path=tmp_path / "adj.parquet")  # fmt: skip
    assert out["ok"] is True and out["bad_days"] == [] and out["recent"] == []


def test_the_check_command_lists_them(tmp_path, monkeypatch, capsys):
    master, adjusted = bill_fund()
    ex = master.index[master["date"] == "2025-12-01"][0]
    master.to_parquet(tmp_path / atr.MASTER_FILE, index=False)
    path = tmp_path / "etf_total_return_closes.parquet"
    without_payout(master, adjusted, ex).to_parquet(path, index=False)
    monkeypatch.setattr(atr, "ADJUSTED_PARQUET", path)
    assert atr.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "1 bad days in IBKR's adjusted closes, all repaired in the signals (1 inside their lookback)" in out
    assert "* BIL 2025-12-01: the price falls" in out


# ── target generation ─────────────────────────────────────────────────────


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A master with a T-bill fund and an equity, and the adjusted closes beside it."""
    bil, bil_adj = bill_fund()
    spy = stock("SPY", step=0.0001)
    prices = tmp_path / "etf_prices_master.parquet"
    pd.concat([bil, spy], ignore_index=True).to_parquet(prices, index=False)
    adjusted = tmp_path / gt.ADJUSTED_FILE
    spy_adj = spy.rename(columns={"close": "adj_close"})
    pd.concat([bil_adj, spy_adj], ignore_index=True).to_parquet(adjusted, index=False)
    paths = {
        "prices_csv": tmp_path / "etf_prices_master.csv",
        "prices_parquet": prices,
        "targets_csv": tmp_path / "t" / "targets.csv",
        "targets_parquet": tmp_path / "t" / "targets.parquet",
    }
    monkeypatch.setattr(gt, "_paths", lambda: paths)
    universe = tmp_path / "etfs.yaml"
    universe.write_text("etfs:\n  signal_prices: total_return\n  core: [BIL, SPY]\n")
    monkeypatch.setattr(gt, "UNIVERSE_PATH", universe)
    return paths, universe, adjusted


def _latest(paths) -> pd.DataFrame:
    t = pd.read_parquet(paths["targets_parquet"])
    return t[t["rebalance_date"] == t["rebalance_date"].max()].set_index("symbol")


def test_targets_rank_on_total_return_and_price_orders_at_the_close(tree):
    paths, _, _ = tree
    out = gt.generate_targets(optimizer="momentum_top5", top_k=1, through=date(2025, 12, 31))
    assert out["signal_prices"] == "total_return" and out["signal_prices_note"] is None
    latest = _latest(paths)
    # BIL's ~2% half-year income beats SPY's ~1.3% price drift only once income counts
    assert list(latest.index) == ["BIL"]
    assert latest.loc["BIL", "reference_price"] == pytest.approx(bill_fund()[0]["close"].iloc[-1])


def test_target_generation_says_what_it_repaired(tree):
    paths, _, adjusted = tree
    master, true = bill_fund()
    ex = master.index[master["date"] == "2025-12-01"][0]
    spy = pd.read_parquet(adjusted)
    spy = spy[spy["symbol"] == "SPY"]
    pd.concat([without_payout(master, true, ex), spy], ignore_index=True).to_parquet(adjusted, index=False)
    out = gt.generate_targets(optimizer="momentum_top5", top_k=1, through=date(2025, 12, 31))
    assert out["signal_prices"] == "total_return"
    assert out["signal_prices_note"].startswith("1 bad day in IBKR's adjusted closes repaired, the latest: BIL 2025-12-01")


def test_on_price_the_same_panel_picks_the_equity(tree):
    paths, universe, _ = tree
    universe.write_text(universe.read_text().replace("total_return", "price"))
    out = gt.generate_targets(optimizer="momentum_top5", top_k=1, through=date(2025, 12, 31))
    assert out["signal_prices"] == "price" and "etfs.signal_prices is price" in out["signal_prices_note"]
    assert list(_latest(paths).index) == ["SPY"]


def test_before_the_first_refresh_targets_run_on_price_and_say_so(tree):
    paths, _, adjusted = tree
    adjusted.unlink()
    out = gt.generate_targets(optimizer="momentum_top5", top_k=1, through=date(2025, 12, 31))
    assert out["signal_prices"] == "price"
    assert out["signal_prices_note"] == "on price, no adjusted closes yet: BIL, SPY"
    assert list(_latest(paths).index) == ["SPY"]


def test_the_setting(tmp_path):
    assert gt.signal_prices_setting() == "total_return"  # the committed model
    path = tmp_path / "etfs.yaml"
    path.write_text("etfs:\n  core: [SPY]\n")
    assert gt.signal_prices_setting(path) == "price"
    path.write_text("etfs:\n  signal_prices: dividends\n  core: [SPY]\n")
    with pytest.raises(ValueError, match="must be one of total_return, price"):
        gt.signal_prices_setting(path)


# ── the runner ────────────────────────────────────────────────────────────


def test_a_failed_refresh_never_fails_the_run(monkeypatch):
    from src.production import daily_runner

    def boom(**_):
        raise ConnectionRefusedError("gateway down")

    monkeypatch.setattr(atr, "append_total_return", boom)
    out = daily_runner.step_update_total_return(host="127.0.0.1", port=4002, client_id=1)
    assert out["ok"] is False and "gateway down" in out["error"]
