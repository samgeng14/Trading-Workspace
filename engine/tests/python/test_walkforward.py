"""The walk-forward harness: no look-ahead, an untouched holdout, folds that honour the embargo,
the deflated Sharpe ratio, and the cost model flowing through."""

import math

import numpy as np
import pytest

wf = pytest.importorskip("dv02_engine.walkforward")
costs = pytest.importorskip("dv02_engine.costs")
pd = pytest.importorskip("pandas")

walk_forward, make_folds = wf.walk_forward, wf.make_folds
CostModel = costs.CostModel
COST_PARTS = ("commission", "spread", "impact", "borrow", "roll")
TIMING_METRICS = {"parsing_time_ms", "simulation_time_ms", "total_time_ms", "simulation_speed_dates_per_sec"}


def panel(rows=160, assets=("A", "B", "C"), seed=1, drift=0.0003):
    """Random-walk closes on business days, one column per asset."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=rows)
    paths = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, (rows, len(assets))), axis=0))
    return pd.DataFrame(paths, index=dates, columns=list(assets))


def label(date):
    return pd.Timestamp(date).strftime("%Y-%m-%d")


def equal_weights(history):
    return {a: 0.3 for a in history.columns}


def trend(history):
    """Weights from the whole history the strategy is given: every row it sees moves them."""
    last = history.iloc[-1] / history.iloc[-6] - 1
    w = 0.25 + 0.1 * np.tanh(20 * last.to_numpy()) + 1e-6 * (history.to_numpy().sum() % 1)
    return dict(zip(history.columns, w))


def assert_same_run(a, b):
    """Two results with exactly the same numbers (the metrics' timings aside)."""
    pd.testing.assert_frame_equal(a.folds, b.folds, check_exact=True)
    pd.testing.assert_frame_equal(a.equity, b.equity, check_exact=True)
    pd.testing.assert_frame_equal(a.events, b.events, check_exact=True)
    np.testing.assert_equal(a.combined, b.combined)
    assert a.holdout == b.holdout
    for name in ("backtest_results", "backtest_positions", "backtest_weights"):
        pd.testing.assert_frame_equal(a.backtest[name], b.backtest[name], check_exact=True)
    ma, mb = a.backtest["backtest_metrics"], b.backtest["backtest_metrics"]
    keep = ~ma["metric"].isin(TIMING_METRICS)
    pd.testing.assert_frame_equal(ma[keep], mb[keep], check_exact=True)


# ── look-ahead ────────────────────────────────────────────────────────


def test_look_ahead_canary():
    prices = panel()
    seen = []

    def strategy(history):
        seen.append((history.index[-1], len(history)))
        return trend(history)

    kw = dict(train=40, test=20, costs=None, n_trials=1, lag=1, rebalance_every=5)
    base = walk_forward(prices, strategy, **kw)
    # the strategy sees the rows up to its decision date and not one more
    assert len(seen) == len(base.events) == 24
    for (last, rows), decided in zip(seen, base.events["decided"]):
        assert label(last) == decided
        assert rows == prices.index.get_loc(last) + 1
    # and every decision trades at the next close
    for decided, executed in zip(base.events["decided"], base.events["executed"]):
        assert prices.index.get_loc(pd.Timestamp(executed)) == prices.index.get_loc(pd.Timestamp(decided)) + 1

    # Scramble every row after a decision row in the middle. The decisions up to that row, and
    # the equity up to it, do not move; the ones after it do, so the canary would sing.
    cut = pd.Timestamp(base.events["decided"].iloc[len(base.events) // 2])
    cut_row = prices.index.get_loc(cut)
    shaken = prices.copy()
    future = shaken.iloc[cut_row + 1 :]
    shaken.iloc[cut_row + 1 :] = future * np.random.default_rng(3).uniform(0.5, 1.5, future.shape)
    seen.clear()
    after = walk_forward(shaken, strategy, **kw)
    before = (pd.to_datetime(base.events["decided"]) <= cut).to_numpy()
    assert before.sum() == 13 and (~before).sum() == 11
    pd.testing.assert_frame_equal(base.events[before], after.events[before], check_exact=True)
    assert not base.events[~before].equals(after.events[~before])
    pd.testing.assert_frame_equal(base.equity.loc[:cut], after.equity.loc[:cut], check_exact=True)
    assert not base.equity.loc[cut:].iloc[1:].equals(after.equity.loc[cut:].iloc[1:])


def test_a_strategy_that_reads_the_next_row_is_caught():
    prices = panel(rows=80)

    def peek(history):
        # shift(-1) puts tomorrow's close on today's row: NaN on the decision row, which has no tomorrow
        return {"A": float(history["A"].shift(-1).iloc[-1] / history["A"].iloc[-1])}

    with pytest.raises(ValueError, match="weights at t may use data up to t only"):
        walk_forward(prices, peek, train=30, test=20, costs=None, n_trials=1)


def test_weights_the_harness_cannot_trade_are_errors():
    prices = panel(rows=80)
    with pytest.raises(ValueError, match="not in the panel"):
        walk_forward(prices, lambda history: {"Z": 0.5}, train=30, test=20, costs=None, n_trials=1)
    with pytest.raises(TypeError, match="mapping or Series"):
        walk_forward(prices, lambda history: [0.5], train=30, test=20, costs=None, n_trials=1)
    # the first decision is on row 30 and trades on row 31, where A has no price
    holes = prices.copy()
    holes.iloc[31, 0] = np.nan
    with pytest.raises(ValueError, match="has no price on 2024-02-14, the trade date"):
        walk_forward(holes, lambda history: {"A": 0.5}, train=30, test=20, costs=None, n_trials=1)
    # a zero weight on it is fine, and so is a Series
    out = walk_forward(
        holes, lambda history: pd.Series({"A": 0.0, "B": 0.5}), train=30, test=20, costs=None, n_trials=1
    )
    assert out.events["B"].tolist() == [0.5] * len(out.events)


# ── the holdout ───────────────────────────────────────────────────────


def test_holdout_is_never_touched():
    prices = panel(rows=160)
    volumes = pd.DataFrame(1e6, index=prices.index, columns=prices.columns)
    holdout = 30
    first_held = prices.index[len(prices) - holdout]
    fit_last, seen_last = [], []

    def fit(train):
        fit_last.append(train.index[-1])
        return float(train.mean().mean())

    def strategy(history, params):
        seen_last.append(history.index[-1])
        return {a: 0.3 for a in history.columns}

    kw = dict(train=40, test=25, gap=3, holdout=holdout, costs=CostModel(), n_trials=3, fit=fit, rebalance_every=5)
    base = walk_forward(prices, strategy, volumes=volumes, **kw)
    assert base.holdout == (label(first_held), label(prices.index[-1]))
    assert max(fit_last) < first_held and max(seen_last) < first_held
    assert base.equity.index.max() < first_held
    assert pd.to_datetime(base.events["executed"]).max() < first_held
    assert pd.to_datetime(base.backtest["backtest_results"]["date"]).max() < first_held
    assert base.folds["test_end"].map(pd.Timestamp).max() < first_held
    # from the first trade (row train + gap + lag) to the last row before the holdout
    assert len(base.equity) == 160 - holdout - (40 + 3 + 1)

    # wreck every held-out price and volume: nothing in the output moves
    wrecked_prices, wrecked_volumes = prices.copy(), volumes.copy()
    wrecked_prices.iloc[-holdout:] = np.nan
    wrecked_volumes.iloc[-holdout:] = np.nan
    assert_same_run(base, walk_forward(wrecked_prices, strategy, volumes=wrecked_volumes, **kw))
    wrecked_prices.iloc[-holdout:] = 1e9
    wrecked_volumes.iloc[-holdout:] = 0.0
    assert_same_run(base, walk_forward(wrecked_prices, strategy, volumes=wrecked_volumes, **kw))
    # without a holdout, there is none to report
    assert walk_forward(prices, strategy, volumes=volumes, **{**kw, "holdout": 0}).holdout is None


# ── folds and the embargo ─────────────────────────────────────────────


def test_fold_boundaries_honour_the_embargo():
    dates = pd.bdate_range("2024-01-01", periods=100)
    for mode in ("expanding", "rolling"):
        folds = make_folds(dates, train=30, test=20, mode=mode, gap=5, holdout=10)
        # 90 rows before the holdout: test blocks of 20 from row 35, the last one short
        assert [f.test for f in folds] == [range(35, 55), range(55, 75), range(75, 90)]
        assert [f.index for f in folds] == [0, 1, 2]
        for f in folds:
            assert f.train.stop + 5 == f.test.start  # exactly the gap between them
            assert f.train.start == (0 if mode == "expanding" else f.test.start - 5 - 30)
            assert f.train_start == label(dates[f.train.start]) and f.train_end == label(dates[f.train.stop - 1])
            assert f.test_start == label(dates[f.test.start]) and f.test_end == label(dates[f.test.stop - 1])
            assert f.test.stop <= 90  # never the holdout
        assert all(a.test.stop == b.test.start for a, b in zip(folds, folds[1:]))
    # no gap: the training window ends the row before its test block; no holdout: the last block takes the rest
    folds = make_folds(dates, train=30, test=40)
    assert [(f.train, f.test) for f in folds] == [(range(0, 30), range(30, 70)), (range(0, 70), range(70, 100))]
    folds = make_folds(dates, train=30, test=40, mode="rolling")
    assert [(f.train, f.test) for f in folds] == [(range(0, 30), range(30, 70)), (range(40, 70), range(70, 100))]
    with pytest.raises(ValueError, match="leave no test block"):
        make_folds(dates[:40], train=30, test=20, gap=5, holdout=5)
    for bad in ({"train": 0}, {"test": 0}, {"gap": -1}, {"holdout": -1}, {"train": 30.0}, {"train": True}):
        with pytest.raises(ValueError, match=next(iter(bad))):
            make_folds(dates, **{"train": 30, "test": 20, **bad})
    with pytest.raises(ValueError, match="mode"):
        make_folds(dates, train=30, test=20, mode="sliding")


def test_fit_sees_the_training_rows_and_decisions_start_after_the_gap():
    prices = panel(rows=120)
    spans, decisions = [], []

    def fit(train):
        spans.append((train.index[0], train.index[-1]))
        return None

    def strategy(history, params):
        decisions.append(history.index[-1])
        return {"A": 0.5}

    out = walk_forward(
        prices, strategy, fit=fit, train=30, test=20, gap=5, holdout=10, costs=None, n_trials=1, rebalance_every=20
    )
    loc = prices.index.get_loc
    # 110 rows before the holdout: blocks of 20 from row 35, the last of 15; one decision per block
    assert len(spans) == len(decisions) == len(out.folds) == 4
    for k, ((first, last), decided) in enumerate(zip(spans, decisions)):
        assert loc(first) == 0 and loc(last) == 35 + 20 * k - 5 - 1
        assert loc(decided) == 35 + 20 * k
    assert out.folds["train_end"].tolist() == [label(s[1]) for s in spans]
    assert out.folds["test_start"].tolist() == [label(d) for d in decisions]
    # out of sample, a fold runs from its first trade (the row after its first decision) to the
    # next fold's first trade, or to the holdout: 20 days each, and 14 for the last block of 15
    assert out.folds["days"].tolist() == [20.0, 20.0, 20.0, 14.0]


def test_lag_sets_the_trade_date_and_drops_trades_into_the_holdout():
    prices = panel(rows=100)
    loc = prices.index.get_loc
    decisions = [40 + 4 * k for k in range(13)]  # blocks [40, 60), [60, 80), [80, 90), every 4th row
    for lag in (0, 1, 3):
        out = walk_forward(
            prices, equal_weights, train=40, test=20, holdout=10, lag=lag, costs=None, n_trials=1, rebalance_every=4
        )
        decided = out.events["decided"].map(lambda d: loc(pd.Timestamp(d))).tolist()
        executed = out.events["executed"].map(lambda d: loc(pd.Timestamp(d))).tolist()
        assert decided == [d for d in decisions if d + lag < 90]  # a trade in the holdout is dropped
        assert executed == [d + lag for d in decided]
        assert out.equity.index[0] == prices.index[40 + lag] and len(out.equity) == 90 - 40 - lag
        # fold k's out-of-sample days run from its first trade date to the next fold's
        first_rows = out.equity.reset_index().groupby("fold")["date"].first().map(loc).tolist()
        assert first_rows == [40 + lag, 60 + lag, 80 + lag]
        assert out.folds["oos_start"].tolist() == [label(prices.index[s + lag]) for s in (40, 60, 80)]
        assert out.folds["days"].sum() == len(out.equity)
    with pytest.raises(ValueError, match="past the last row before the holdout"):
        walk_forward(prices, equal_weights, train=40, test=20, holdout=10, lag=50, costs=None, n_trials=1)


# ── statistics ────────────────────────────────────────────────────────


def test_deflated_sharpe_matches_hand_computed_values():
    # 101 daily returns with a Sharpe of 0.1 a day, normal (skew 0, kurtosis 3), so the trials'
    # variance is the estimation noise 1 / 100. One trial: nothing to deflate, and
    # DSR = Phi(0.1 x sqrt(100) / sqrt(1 + 0.1^2 / 2)) = Phi(0.99751) = 0.840741
    dsr, threshold = wf.deflated_sharpe_ratio(0.1, 101, 1)
    assert threshold == 0.0 and dsr == pytest.approx(0.840741, abs=1e-6)
    assert wf.probabilistic_sharpe_ratio(0.1, 0.0, 101) == pytest.approx(0.840741, abs=1e-6)
    # two trials: SR0 = sqrt(1/100) x ((1 - 0.5772) Phi^-1(1/2) + 0.5772 Phi^-1(1 - 1/(2e)))
    #                 = 0.1 x (0 + 0.5772 x 0.90045) = 0.051976
    # DSR = Phi((0.1 - 0.051976) x 10 / sqrt(1.005)) = Phi(0.47904) = 0.684048
    dsr, threshold = wf.deflated_sharpe_ratio(0.1, 101, 2)
    assert threshold == pytest.approx(0.051976, abs=1e-6) and dsr == pytest.approx(0.684048, abs=1e-6)
    # five: Phi^-1(0.8) = 0.84162 and Phi^-1(1 - 1/(5e)) = 1.44967, so SR0 = 0.1 x (0.4228 x 0.84162
    # + 0.5772 x 1.44967) = 0.119259, above the 0.1 observed: DSR = Phi(-0.19213) = 0.423826
    dsr, threshold = wf.deflated_sharpe_ratio(0.1, 101, 5)
    assert threshold == pytest.approx(0.119259, abs=1e-6) and dsr == pytest.approx(0.423826, abs=1e-6)
    # a fat left tail (skew -0.5, kurtosis 5) makes the denominator sqrt(1 + 0.05 + 0.01)
    assert wf.deflated_sharpe_ratio(0.1, 101, 5, None, -0.5, 5.0)[0] == pytest.approx(0.425805, abs=1e-6)
    # the trials' own variance, 0.0004 a day, over ten trials: SR0 = 0.02 x (0.4228 x 1.28155
    # + 0.5772 x 1.78975) = 0.031492, DSR = Phi((0.1 - 0.031492) x 10 / sqrt(1.005)) = 0.752815
    dsr, threshold = wf.deflated_sharpe_ratio(0.1, 101, 10, 0.0004)
    assert threshold == pytest.approx(0.031492, abs=1e-6) and dsr == pytest.approx(0.752815, abs=1e-6)
    assert wf.expected_max_sharpe(10, 0.0004) == pytest.approx(0.031492, abs=1e-6)
    assert wf.expected_max_sharpe(1, 0.01) == 0.0
    assert wf.sharpe_standard_error(1.0, 4.0) == pytest.approx(math.sqrt(1.5 / 4))
    # undefined inputs are NaN, not errors; impossible ones are errors
    assert all(math.isnan(x) for x in wf.deflated_sharpe_ratio(0.1, 1, 5))
    assert math.isnan(wf.deflated_sharpe_ratio(math.nan, 101, 5)[0])
    assert math.isnan(wf.sharpe_standard_error(1.0, 0.0)) and math.isnan(wf.sharpe_standard_error(math.nan, 1.0))
    with pytest.raises(ValueError, match="n_trials"):
        wf.expected_max_sharpe(0, 0.01)
    with pytest.raises(ValueError, match="variance"):
        wf.expected_max_sharpe(5, -0.01)


def test_deflated_sharpe_falls_as_trials_grow():
    trials = (1, 2, 5, 10, 50, 500)
    dsr = [wf.deflated_sharpe_ratio(0.1, 101, n)[0] for n in trials]
    thresholds = [wf.deflated_sharpe_ratio(0.1, 101, n)[1] for n in trials]
    assert dsr == sorted(dsr, reverse=True) and thresholds == sorted(thresholds)
    assert dsr[0] > 0.84 and dsr[-1] < 0.03
    assert [round(x, 6) for x in dsr] == [0.840741, 0.684048, 0.423826, 0.283266, 0.101487, 0.020309]


def test_harness_deflates_the_stitched_record():
    prices = panel(rows=160, drift=0.001)
    kw = dict(train=40, test=20, costs=None, rebalance_every=10)
    runs = [walk_forward(prices, equal_weights, n_trials=n, **kw) for n in (1, 10, 100)]
    dsr = [r.combined["deflated_sharpe"] for r in runs]
    thresholds = [r.combined["deflated_sharpe_threshold"] for r in runs]
    assert dsr == sorted(dsr, reverse=True) and dsr[0] > dsr[-1]
    assert thresholds[0] == 0.0 and thresholds == sorted(thresholds) and thresholds[-1] > thresholds[1]
    # the deflation is the only thing that changes
    for r in runs[1:]:
        pd.testing.assert_frame_equal(r.folds, runs[0].folds)
        pd.testing.assert_frame_equal(r.equity, runs[0].equity)
    # and it is the function applied to the stitched daily returns
    r = runs[1].equity["daily_return"].to_numpy()
    mean, std = r.mean(), r.std(ddof=1)
    centred = r - mean
    skew = (centred**3).mean() / (centred**2).mean() ** 1.5
    kurtosis = (centred**4).mean() / (centred**2).mean() ** 2
    dsr_by_hand, sr0 = wf.deflated_sharpe_ratio(mean / std, len(r), 10, None, skew, kurtosis)
    combined = runs[1].combined
    assert combined["deflated_sharpe"] == pytest.approx(dsr_by_hand)
    assert combined["deflated_sharpe_threshold"] == pytest.approx(sr0 * math.sqrt(252))
    assert combined["sharpe"] == pytest.approx(mean / std * math.sqrt(252))
    assert combined["sharpe_se"] == pytest.approx(wf.sharpe_standard_error(combined["sharpe"], len(r) / 252))
    assert combined["n_trials"] == 10 and combined["folds"] == 6 and combined["days"] == len(r)
    assert combined["skew"] == pytest.approx(skew) and combined["kurtosis"] == pytest.approx(kurtosis)
    # the trials' own Sharpe ratios set the bar from their spread
    sharpes = [0.5, 1.0, 1.5, -0.2, 0.8, 0.1, 1.2, 0.3, 0.9, 0.6]
    spread = walk_forward(prices, equal_weights, n_trials=10, trial_sharpes=sharpes, **kw)
    expected = wf.expected_max_sharpe(10, float(np.var(sharpes, ddof=1)) / 252) * math.sqrt(252)
    assert spread.combined["deflated_sharpe_threshold"] == pytest.approx(expected)


def test_bad_n_trials_is_rejected_before_the_run():
    prices = panel(rows=80)
    calls = []

    def strategy(history):
        calls.append(1)
        return {"A": 0.5}

    kw = dict(train=30, test=20, costs=None)
    for bad in (0, -3, 1.5, True, "12", None):
        with pytest.raises(ValueError, match="n_trials"):
            walk_forward(prices, strategy, n_trials=bad, **kw)
    assert calls == []
    for name, bad in (("rebalance_every", 0), ("lag", -1)):
        with pytest.raises(ValueError, match=name):
            walk_forward(prices, strategy, n_trials=1, **{name: bad}, **kw)
    with pytest.raises(ValueError, match="trial_sharpes"):
        walk_forward(prices, strategy, n_trials=2, trial_sharpes=[1.0], **kw)
    with pytest.raises(TypeError, match="costs must be"):
        walk_forward(prices, strategy, n_trials=1, train=30, test=20, costs="free")
    assert calls == []


def test_a_book_that_loses_everything_is_minus_100_percent_a_year():
    # a 300% book (flagged by the mandate check, never rescaled) is wiped out when every asset
    # loses 60% in a day: -100% a year, not a complex number, and finite everywhere else
    prices = panel(rows=80)
    prices.iloc[50:] *= 0.4
    out = walk_forward(
        prices, lambda history: {"A": 3.0}, train=30, test=20, costs=None, n_trials=1, rebalance_every=100
    )
    assert out.combined["annual_return"] == -1.0
    assert out.combined["mandate_breach_days"] == out.combined["days"]
    assert out.folds["annual_return"].dtype == np.float64
    for name in ("sharpe", "annual_volatility", "max_drawdown"):
        assert math.isfinite(out.combined[name]), name
    assert out.combined["max_drawdown"] < -1.0


# ── costs ─────────────────────────────────────────────────────────────


def test_costs_flow_through_and_cash_earns_interest():
    """A 90% book holds 10% cash, which earns the model's rate (the smoke run that earned
    nothing held 5% cash under a default rate of 0)."""
    prices = panel(rows=120)
    kw = dict(train=30, test=30, n_trials=1, rebalance_every=10)
    free = walk_forward(prices, equal_weights, costs=None, **kw)
    assert "interest_income" not in free.combined and "commission_cost" not in free.folds.columns
    paid = walk_forward(prices, equal_weights, costs=CostModel(), **kw)  # the defaults, interest included
    assert paid.combined["interest_income"] > 0
    assert paid.combined["commission_cost"] > 0 and paid.combined["spread_cost"] > 0
    assert paid.combined["impact_cost"] == 0  # no volumes: unmeasured, and not charged
    assert paid.combined["borrow_cost"] == 0 and paid.combined["roll_cost"] == 0
    parts = sum(paid.combined[f"{part}_cost"] for part in COST_PARTS)
    assert paid.combined["total_cost"] == pytest.approx(parts - paid.combined["interest_income"])
    assert paid.folds["interest_income"].sum() == pytest.approx(paid.combined["interest_income"])
    assert paid.folds["total_cost"].sum() == pytest.approx(paid.combined["total_cost"])
    assert paid.combined["cost_drag"] == pytest.approx(
        paid.combined["total_cost"] / (paid.combined["days"] / 252) / paid.equity["portfolio_value"].mean()
    )
    # day by day: the previous close's cash at the rate, actual/360 on the calendar days between closes
    results, positions = paid.backtest["backtest_results"], paid.backtest["backtest_positions"]
    days = np.diff(pd.to_datetime(results["date"]).values).astype("timedelta64[D]").astype(int)
    rate = CostModel().cash_interest_bps / 10_000
    expected = [0.0] + [positions["cash"].iloc[k] * rate * int(days[k]) / 360 for k in range(len(days))]
    assert results["daily_interest_income"].tolist() == pytest.approx(expected)
    assert max(days) == 3 and sum(expected) > 0
    # a higher rate earns proportionally more (the balances compound a little differently)
    four = walk_forward(prices, equal_weights, costs=CostModel(cash_interest_bps=400.0), **kw)
    ratio = four.combined["interest_income"] / paid.combined["interest_income"]
    assert ratio == pytest.approx(400 / CostModel().cash_interest_bps, rel=1e-2)
    # with interest off, the costs leave the book below the free run on every day
    bare = walk_forward(prices, equal_weights, costs=CostModel(cash_interest_bps=0.0), **kw)
    assert bare.combined["interest_income"] == 0
    assert (bare.equity["portfolio_value"] < free.equity["portfolio_value"]).all()
    assert bare.combined["total_cost"] > paid.combined["total_cost"]


def test_a_date_column_is_the_same_as_a_datetime_index():
    prices = panel(rows=80)
    with_column = prices.reset_index().rename(columns={"index": "date"})
    with_column["date"] = with_column["date"].dt.strftime("%Y/%m/%d")
    kw = dict(train=30, test=20, costs=CostModel(), n_trials=2, rebalance_every=5)
    assert_same_run(walk_forward(prices, trend, **kw), walk_forward(with_column, trend, **kw))
    with pytest.raises(ValueError, match="DatetimeIndex"):
        walk_forward(prices.reset_index(drop=True), trend, **kw)
    with pytest.raises(ValueError, match="increasing"):
        walk_forward(prices.iloc[::-1], trend, **kw)
