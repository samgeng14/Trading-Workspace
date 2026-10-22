"""The cost model from Python: the bindings carry the C++ numbers, and the backtester charges them."""

import math

import numpy as np
import pytest

costs = pytest.importorskip("dv02_engine.costs")
bt = pytest.importorskip("dv02_engine.backtest")
pd = pytest.importorskip("pandas")

CostModel = costs.CostModel
HawkBacktester = bt.HawkBacktester

# the metrics that measure the run, not the book
TIMING_METRICS = {"parsing_time_ms", "simulation_time_ms", "total_time_ms", "simulation_speed_dates_per_sec"}


def free_model(**overrides):
    """Every cost component off; a test switches on the one it checks."""
    settings = {
        "commissions": False,
        "half_spread_bps": 0.0,
        "impact_k": 0.0,
        "borrow_bps": 0.0,
        "cash_interest_bps": 0.0,
        "futures": {
            root: costs.FuturesRoot(spec.commission, 0.0, spec.roll_months, spec.roll_day)
            for root, spec in costs.default_futures().items()
        },
    }
    settings.update(overrides)
    return CostModel(**settings)


def flat(dates, **values):
    """A price (or volume) frame with one constant value per asset."""
    return pd.DataFrame({"date": list(dates), **{a: [float(v)] * len(dates) for a, v in values.items()}})


def event(date, **weights):
    """One weight event."""
    return pd.DataFrame({"date": [date], **{a: [float(w)] for a, w in weights.items()}})


def metric(out, name):
    m = out["backtest_metrics"]
    return float(m.loc[m["metric"] == name, "value"].iloc[0])


def calendar_days(dates):
    """Calendar days between consecutive dates."""
    return [int(d) for d in np.diff(pd.to_datetime(list(dates)).values).astype("timedelta64[D]").astype(int)]


# ── commission ────────────────────────────────────────────────────────


def test_ibkr_pro_fixed_schedule():
    ibkr = costs.ibkr_pro_fixed
    assert ibkr(50_000, 50) == pytest.approx(5.0)  # 1,000 shares at $0.005
    assert ibkr(1_000, 100) == 1.0  # 10 shares would be $0.05: the $1.00 minimum
    assert ibkr(150, 1.0) == 1.0  # 150 shares, $0.75: the minimum again
    assert ibkr(50_000, 0.10) == pytest.approx(500.0)  # 500,000 shares, $2,500, capped at 1% of $50,000
    assert ibkr(50, 10) == pytest.approx(0.5)  # 5 shares: the 1% cap, $0.50, beats the $1.00 minimum
    assert ibkr(100, 10) == 1.0  # 1% of $100 is the minimum exactly
    assert ibkr(0, 10) == 0 and ibkr(-50_000, 50) == 0 and ibkr(100, 0) == 0
    assert ibkr(math.nan, 50) == 0 and ibkr(50_000, math.nan) == 0  # unmeasured is free, not $1.00


def test_commission_charged_by_the_backtester():
    # 50% of $100,000 in A at $50 is 1,000 shares: $5.00; 10% in B at $200 is 50 shares, $0.25, lifted to $1.00
    prices = flat(["2024-01-02", "2024-01-03"], A=50.0, B=200.0)
    weights = event("2024-01-02", A=0.5, B=0.1)
    out = HawkBacktester(100_000, costs=free_model(commissions=True)).run(prices, weights)
    r = out["backtest_results"]
    assert r["daily_commission_cost"].tolist() == pytest.approx([6.0, 0.0])
    assert r["daily_total_cost"].tolist() == pytest.approx([6.0, 0.0])
    assert r["daily_slippage_cost"].tolist() == [0.0, 0.0]
    assert metric(out, "cumulative_commission_cost") == pytest.approx(6.0)
    assert out["backtest_positions"]["cash"].iloc[0] == pytest.approx(40_000 - 6.0)
    # the schedule the plain backtester's fee model charges
    plain = HawkBacktester(100_000, fee_model="ibkr_pro_fixed").run(prices, weights)
    assert plain["backtest_results"]["daily_commission_cost"].tolist() == r["daily_commission_cost"].tolist()
    # futures pay per contract: 50% of $100,000 in MES at 5,000 x $5 = $25,000 a contract is 2 contracts at $0.62
    prices = flat(["2024-01-02", "2024-01-03"], MES=5_000.0)
    model = free_model(commissions=True, instruments={"MES": costs.future(5)})
    out = HawkBacktester(100_000, costs=model).run(prices, event("2024-01-02", MES=0.5))
    assert out["backtest_results"]["daily_commission_cost"].iloc[0] == pytest.approx(1.24)
    assert costs.futures_commission(50_000, 5_000, 5, 0.62) == pytest.approx(1.24)


# ── spread ────────────────────────────────────────────────────────────


def test_half_spread():
    assert costs.spread_cost(100_000, 2.0) == pytest.approx(20.0)  # 2 bps of $100,000
    assert costs.spread_cost(50_000, 0.5) == pytest.approx(2.5)
    assert costs.spread_cost(0, 2.0) == 0
    assert costs.spread_cost(-100_000, 2.0) == 0  # the dollars traded are unsigned: a sale is not a refund
    assert costs.spread_cost(math.nan, 2.0) == 0
    # the backtester: $50,000 of A at the 2 bps default, $30,000 of B at a 0.5 bp override
    prices = flat(["2024-01-02", "2024-01-03", "2024-01-04"], A=100.0, B=200.0)
    weights = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "A": [0.5, 0.2], "B": [0.3, 0.3]})
    model = free_model(half_spread_bps=2.0, half_spread_bps_by_symbol={"B": 0.5})
    out = HawkBacktester(100_000, costs=model).run(prices, weights)
    r = out["backtest_results"]
    assert r["daily_spread_cost"].iloc[0] == pytest.approx(10.0 + 1.5)
    assert r["daily_slippage_cost"].iloc[0] == pytest.approx(11.5)  # spread plus impact
    assert r["daily_impact_cost"].iloc[0] == 0
    # day 2 on $99,988.50: A 50,000 -> 19,997.70 is $30,002.30 at 2 bps; B stays at 30% of value, $29,996.55 -> ...
    value = 100_000 - 11.5
    sold_a = 50_000 - 0.2 * value
    moved_b = abs(0.3 * value - 30_000)
    assert r["daily_spread_cost"].iloc[1] == pytest.approx(sold_a * 2e-4 + moved_b * 0.5e-4)
    assert r["daily_spread_cost"].iloc[2] == 0
    assert metric(out, "cumulative_spread_cost") == pytest.approx(r["daily_spread_cost"].sum())


# ── impact ────────────────────────────────────────────────────────────


def test_square_root_impact_scaling():
    # k 0.5, 2% daily vol, $1m traded against $100m ADV: 0.5 x 0.02 x sqrt(0.01) = 10 bps, $1,000
    assert costs.impact_bps(1e6, 0.02, 1e8, 0.5) == pytest.approx(10.0)
    assert costs.impact_cost(1e6, 0.02, 1e8, 0.5) == pytest.approx(1_000.0)
    # four times the shares: twice the cost per share, eight times the dollars
    assert costs.impact_bps(4e6, 0.02, 1e8, 0.5) == pytest.approx(2 * costs.impact_bps(1e6, 0.02, 1e8, 0.5))
    assert costs.impact_cost(4e6, 0.02, 1e8, 0.5) == pytest.approx(8 * costs.impact_cost(1e6, 0.02, 1e8, 0.5))
    per_share = [costs.impact_bps(size, 0.02, 1e8, 0.5) for size in (1e5, 4e5, 1.6e6, 6.4e6)]
    assert np.diff(per_share) / per_share[:-1] == pytest.approx([1.0, 1.0, 1.0])  # each 4x doubles it
    # k scales it; nothing traded, or k = 0, is free; unmeasured inputs are NaN, not free
    assert costs.impact_bps(1e6, 0.02, 1e8, 1.0) == pytest.approx(20.0)
    assert costs.impact_bps(0, 0.02, 1e8, 0.5) == 0 and costs.impact_bps(1e6, 0.02, 1e8, 0.0) == 0
    assert costs.impact_cost(math.nan, 0.02, 1e8, 0.5) == 0 and costs.impact_cost(-1e6, 0.02, 1e8, 0.5) == 0
    assert math.isnan(costs.impact_bps(1e6, math.nan, 1e8, 0.5))
    assert math.isnan(costs.impact_bps(1e6, 0.02, 0.0, 0.5))
    assert math.isnan(costs.impact_cost(1e6, 0.02, math.nan, 0.5))


def impact_prices():
    # A: +2%, -2%, +2% before the trade date, Jan 8, whose own price and volume must not count
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    return pd.DataFrame({"date": dates, "A": [100.0, 102.0, 99.96, 101.9592, 101.9592]})


def impact_volumes():
    return pd.DataFrame({"date": impact_prices()["date"], "A": [1_000.0, 1_000.0, 2_000.0, 3_000.0, 1e12]})


def test_impact_in_the_backtester_reads_history_before_the_trade_only():
    weights = event("2024-01-08", A=0.5)
    model = free_model(impact_k=0.5, impact_lookback=3)
    out = HawkBacktester(100_000, costs=model).run(impact_prices(), weights, impact_volumes())
    # volatility from the closes of Jan 2-5 (returns +2%, -2%, +2%) is 0.04 / sqrt(3); ADV from
    # Jan 3-5 is (102 x 1,000 + 99.96 x 2,000 + 101.9592 x 3,000) / 3 = $202,599.20
    sigma, adv = 0.04 / math.sqrt(3), 607_797.6 / 3
    expected = 0.5 * sigma * math.sqrt(50_000 / adv) * 50_000
    assert 286 < expected < 287
    r = out["backtest_results"]
    assert r["daily_impact_cost"].iloc[0] == pytest.approx(expected)
    assert r["daily_slippage_cost"].iloc[0] == pytest.approx(expected)
    assert metric(out, "unmeasured_impact_trades") == 0
    # the trade date's own price and volume are never read
    prices, volumes = impact_prices(), impact_volumes()
    prices.loc[4, "A"], volumes.loc[4, "A"] = 500.0, 1.0
    same = HawkBacktester(100_000, costs=model).run(prices, weights, volumes)
    assert same["backtest_results"]["daily_impact_cost"].iloc[0] == pytest.approx(expected)
    # the day before is: half of Jan 5's volume lowers the ADV and raises the impact
    volumes = impact_volumes()
    volumes.loc[3, "A"] = 1_500.0
    more = HawkBacktester(100_000, costs=model).run(impact_prices(), weights, volumes)
    assert more["backtest_results"]["daily_impact_cost"].iloc[0] > expected
    # four times the trade: twice the cost per dollar, eight times the dollars
    big = HawkBacktester(400_000, costs=model).run(impact_prices(), weights, impact_volumes())
    assert big["backtest_results"]["daily_impact_cost"].iloc[0] == pytest.approx(8 * expected)
    # no volumes: no impact, the spread only, and the trade is counted as unmeasured
    spread_only = free_model(impact_k=0.5, impact_lookback=3, half_spread_bps=2.0)
    none = HawkBacktester(100_000, costs=spread_only).run(impact_prices(), weights)
    assert none["backtest_results"]["daily_impact_cost"].iloc[0] == 0
    assert none["backtest_results"]["daily_spread_cost"].iloc[0] == pytest.approx(10.0)
    assert metric(none, "unmeasured_impact_trades") == 1


# ── borrow and interest ───────────────────────────────────────────────


def test_borrow_accrues_actual_360_on_calendar_days():
    # $100,000 short at 25 bps a year over a weekend: 100,000 x 0.0025 x 3 / 360
    assert costs.borrow_cost(-100_000, 25.0, 3) == pytest.approx(100_000 * 0.0025 * 3 / 360)
    assert costs.borrow_cost(100_000, 25.0, 3) == costs.borrow_cost(-100_000, 25.0, 3)  # the sign doesn't matter
    assert costs.borrow_cost(-36_000, 1_000.0, 1) == pytest.approx(10.0)  # hard to borrow: 10% for a day
    assert costs.borrow_cost(-100_000, 25.0, 0) == 0
    assert costs.borrow_cost(-100_000, 25.0, -1) == 0  # a day count never runs backwards: no refund
    assert costs.borrow_cost(math.nan, 25.0, 1) == 0 and costs.borrow_cost(-100_000, 25.0, math.nan) == 0
    # the backtester: a $50,000 short from Friday Jan 5 pays 3 days on Monday and 1 on Tuesday
    dates = ["2024-01-05", "2024-01-08", "2024-01-09"]
    out = HawkBacktester(100_000, costs=free_model(borrow_bps=25.0)).run(flat(dates, A=100.0), event(dates[0], A=-0.5))
    r = out["backtest_results"]
    assert calendar_days(dates) == [3, 1]
    expected = [0.0, 50_000 * 0.0025 * 3 / 360, 50_000 * 0.0025 * 1 / 360]
    assert r["daily_borrow_cost"].tolist() == pytest.approx(expected)
    assert r["daily_total_cost"].tolist() == pytest.approx(expected)  # borrow costs; it never earns
    assert r["portfolio_value"].iloc[-1] == pytest.approx(100_000 - sum(expected))
    assert metric(out, "cumulative_borrow_cost") == pytest.approx(sum(expected))
    # a long pays nothing; a short future pays nothing
    borrow_only = free_model(borrow_bps=25.0)
    long_run = HawkBacktester(100_000, costs=borrow_only).run(flat(dates, A=100.0), event(dates[0], A=0.5))
    assert metric(long_run, "cumulative_borrow_cost") == 0
    model = free_model(borrow_bps=25.0, instruments={"A": costs.future(1, "ES")})
    short_future = HawkBacktester(100_000, costs=model).run(flat(dates, A=100.0), event(dates[0], A=-0.5))
    assert metric(short_future, "cumulative_borrow_cost") == 0


def test_interest_accrues_actual_360_on_calendar_days():
    # $360,000 at 4% for one day is $40; for a weekend $120
    assert costs.cash_interest(360_000, 400.0, 1) == pytest.approx(40.0)
    assert costs.cash_interest(360_000, 400.0, 3) == pytest.approx(120.0)
    assert costs.cash_interest(-360_000, 400.0, 1) == 0  # a margin loan earns nothing (and is not charged here)
    assert costs.cash_interest(360_000, 0.0, 1) == 0
    assert costs.cash_interest(360_000, 400.0, -1) == 0 and costs.cash_interest(math.nan, 400.0, 1) == 0


def test_cash_only_book_earns_the_configured_rate():
    # all cash from Friday Jan 5 at 4% a year: Monday earns 3 days, then one a day, on the growing balance
    dates = ["2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-16"]
    four_percent = free_model(cash_interest_bps=400.0)
    out = HawkBacktester(100_000, costs=four_percent).run(flat(dates, A=100.0), event(dates[0], A=0.0))
    r, p = out["backtest_results"], out["backtest_positions"]
    assert p["A"].tolist() == [0.0] * len(dates) and p["cash"].iloc[0] == 100_000
    days = calendar_days(dates)
    assert days == [3, 1, 1, 1, 1, 4]  # a weekend, then Martin Luther King Day
    expected = [0.0] + [p["cash"].iloc[k] * 0.04 * days[k] / 360 for k in range(len(days))]
    assert r["daily_interest_income"].tolist() == pytest.approx(expected)
    assert r["daily_interest_income"].iloc[1] == pytest.approx(100_000 * 0.04 * 3 / 360)  # $33.33 over the weekend
    assert r["daily_total_cost"].tolist() == pytest.approx([-x for x in expected])  # income is a negative cost
    assert r["portfolio_value"].iloc[-1] == pytest.approx(100_000 + sum(expected))
    assert metric(out, "cumulative_interest_income") == pytest.approx(sum(expected))
    assert metric(out, "cumulative_total_cost") == pytest.approx(-sum(expected))
    # at 0 the component is off: the same book earns nothing
    off = HawkBacktester(100_000, costs=free_model()).run(flat(dates, A=100.0), event(dates[0], A=0.0))
    assert off["backtest_results"]["portfolio_value"].tolist() == [100_000.0] * len(dates)
    # short proceeds earn nothing, futures notional does, a margin loan neither earns nor pays
    for weight, cash_that_earns in ((-0.5, 100_000), (1.5, 0)):
        out = HawkBacktester(100_000, costs=four_percent).run(flat(dates, A=100.0), event(dates[0], A=weight))
        monday = out["backtest_results"]["daily_interest_income"].iloc[1]
        assert monday == pytest.approx(cash_that_earns * 0.04 * 3 / 360)
    model = free_model(cash_interest_bps=400.0, instruments={"MES": costs.future(5)})
    out = HawkBacktester(100_000, costs=model).run(flat(dates, MES=5_000.0), event(dates[0], MES=0.5))
    assert out["backtest_results"]["daily_interest_income"].iloc[1] == pytest.approx(100_000 * 0.04 * 3 / 360)


def test_default_model_earns_interest_on_cash():
    """A walk-forward smoke run once held 5% cash and earned nothing: the default rate was 0, which is off."""
    model = CostModel()
    rate = model.cash_interest_bps / 10_000
    assert rate > 0
    assert CostModel(cash_interest_bps=0.0).cash_interest_bps == 0  # off when asked
    dates = ["2024-01-05", "2024-01-08", "2024-01-09"]
    out = HawkBacktester(100_000, costs=model).run(flat(dates, A=100.0), event(dates[0], A=0.95))
    r, p = out["backtest_results"], out["backtest_positions"]
    # Friday leaves $5,000 less that day's $4.75 commission and $19 spread in cash (no volumes:
    # no impact); Monday earns three days on it, Tuesday one
    assert p["cash"].iloc[0] == pytest.approx(5_000 - 4.75 - 19.0)
    expected = [0.0, p["cash"].iloc[0] * rate * 3 / 360, p["cash"].iloc[1] * rate * 1 / 360]
    assert r["daily_interest_income"].tolist() == pytest.approx(expected)
    assert r["daily_interest_income"].iloc[1] > 1.0  # about $1.40 at 3.38%
    assert metric(out, "cumulative_interest_income") == pytest.approx(sum(expected))
    assert r["portfolio_value"].iloc[-1] == pytest.approx(100_000 - 4.75 - 19.0 + sum(expected))


# ── every component off ───────────────────────────────────────────────


def test_every_component_off_is_the_plain_backtest_bit_for_bit():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2024-01-02", periods=120).strftime("%Y-%m-%d").tolist()
    paths = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (len(dates), 3)), axis=0))
    prices = pd.DataFrame({"date": dates, "A": paths[:, 0], "B": paths[:, 1], "C": paths[:, 2]})
    shares = rng.integers(100_000, 1_000_000, (len(dates), 3)).astype(float)
    volumes = pd.DataFrame({"date": dates, "A": shares[:, 0], "B": shares[:, 1], "C": shares[:, 2]})
    # long/short rebalances every 7 price dates from the fourth, some days over 100% gross
    events = dates[3::7]
    w = rng.uniform(-0.5, 0.8, (len(events), 3))
    weights = pd.DataFrame({"date": events, "A": w[:, 0], "B": w[:, 1], "C": w[:, 2]})

    plain = HawkBacktester(100_000).run(prices, weights)
    assert len(plain["backtest_results"]) == 117
    costed_runs = (
        HawkBacktester(100_000, costs=free_model()).run(prices, weights),
        HawkBacktester(100_000, costs=free_model()).run(prices, weights, volumes),
    )
    for costed in costed_runs:
        for name in ("backtest_results", "backtest_positions", "backtest_weights"):
            a, b = plain[name], costed[name]
            assert list(b.columns)[: len(a.columns)] == list(a.columns)
            for column in a.columns:
                assert a[column].tolist() == b[column].tolist(), (name, column)  # exactly, not nearly
        r = costed["backtest_results"]
        for column in bt.COST_COLUMNS:
            assert r[column].tolist() == [0.0] * len(r), column
        plain_metrics = dict(zip(plain["backtest_metrics"]["metric"], plain["backtest_metrics"]["value"]))
        costed_metrics = dict(zip(costed["backtest_metrics"]["metric"], costed["backtest_metrics"]["value"]))
        for name, value in plain_metrics.items():
            if name not in TIMING_METRICS:
                assert costed_metrics[name] == value, name
        assert costed_metrics["cumulative_total_cost"] == 0.0
        assert costed_metrics["unmeasured_impact_trades"] == 0.0  # impact off: nothing was measured or missed


# ── errors ────────────────────────────────────────────────────────────


def test_model_and_settings_errors():
    with pytest.raises(ValueError, match="impact_k"):
        CostModel(impact_k=-1.0)
    with pytest.raises(ValueError, match="cash_interest_bps"):
        CostModel(cash_interest_bps=-1.0)
    with pytest.raises(ValueError, match="impact_lookback"):
        CostModel(impact_lookback=1)
    with pytest.raises(ValueError, match="root CL"):
        CostModel(instruments={"CLZ6": costs.future(1_000, "CL")})
    with pytest.raises(ValueError, match="roll_day"):
        costs.FuturesRoot(2.5, roll_day=0)
    with pytest.raises(ValueError, match="costs replaces slippage_bps and fee_model"):
        HawkBacktester(1_000, slippage_bps=1.0, costs=free_model())
    with pytest.raises(ValueError, match="costs replaces slippage_bps and fee_model"):
        HawkBacktester(1_000, fee_model="ibkr_pro_fixed", costs=free_model())
    with pytest.raises(TypeError, match="costs must be"):
        HawkBacktester(1_000, costs="ibkr")
    prices = flat(["2024-01-02"], A=1.0)
    with pytest.raises(ValueError, match="volumes_df is read only by a cost model"):
        HawkBacktester(1_000).run(prices, event("2024-01-02", A=0.5), prices)
