"""HawkBacktester from Python: the Rust package's call, frames and errors, now over C++."""

import math
import subprocess
import sys

import numpy as np
import pytest

bt = pytest.importorskip("dv02_engine.backtest")
pd = pytest.importorskip("pandas")

HawkBacktester = bt.HawkBacktester

RESULT_COLUMNS = [
    "date",
    "portfolio_value",
    "daily_return",
    "daily_log_return",
    "cumulative_return",
    "cumulative_log_return",
    "drawdown",
    "volume_traded",
    "daily_slippage_cost",
    "daily_commission_cost",
]
METRICS = [
    "total_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "avg_drawdown",
    "avg_daily_return",
    "win_rate",
    "num_trades",
    "cumulative_volume_traded",
    "portfolio_turnover",
    "holding_period_years",
    "cumulative_slippage_cost",
    "cumulative_commission_cost",
    "num_price_points",
    "num_weight_events",
    "parsing_time_ms",
    "simulation_time_ms",
    "total_time_ms",
    "simulation_speed_dates_per_sec",
]


def prices_ab():
    return pd.DataFrame(
        {"date": ["2023-01-01", "2023-01-02", "2023-01-03"], "A": [10.0, 11.0, 12.0], "B": [20.0, 19.0, 18.0]}
    )


def weights_ab():
    return pd.DataFrame({"date": ["2023-01-01"], "A": [0.5], "B": [0.3]})


def metric(out, name):
    m = out["backtest_metrics"]
    if isinstance(m, pd.DataFrame):
        return float(m.loc[m["metric"] == name, "value"].iloc[0])
    return float(m.filter(m["metric"] == name)["value"][0])


# ── frames ────────────────────────────────────────────────────────────


def test_pandas_in_pandas_out():
    out = HawkBacktester(initial_value=1000).run(prices_ab(), weights_ab())
    assert list(out) == ["backtest_results", "backtest_positions", "backtest_weights", "backtest_metrics"]
    assert all(isinstance(f, pd.DataFrame) for f in out.values())
    r = out["backtest_results"]
    assert list(r.columns) == RESULT_COLUMNS
    assert list(r["date"]) == ["2023-01-01", "2023-01-02", "2023-01-03"]
    # 500 * 11/10 + 300 * 19/20 + 200
    assert r["portfolio_value"].tolist() == pytest.approx([1000, 1035, 1070])
    assert r["volume_traded"].tolist() == pytest.approx([800, 0, 0])
    p = out["backtest_positions"]
    assert list(p.columns) == ["date", "A", "B", "cash"]
    assert p.iloc[0][["A", "B", "cash"]].tolist() == pytest.approx([500, 300, 200])
    w = out["backtest_weights"]
    assert list(w.columns) == ["date", "A", "B", "cash"]
    assert w.iloc[0][["A", "B", "cash"]].tolist() == pytest.approx([0.5, 0.3, 0.2])
    m = out["backtest_metrics"]
    assert list(m.columns) == ["metric", "value"]
    assert m["metric"].tolist() == METRICS
    assert m["value"].dtype == np.float64


def test_polars_in_polars_out():
    pl = pytest.importorskip("polars")
    prices = pl.from_dict(prices_ab().to_dict(orient="list"))
    weights = pl.from_dict(weights_ab().to_dict(orient="list"))
    out = HawkBacktester(initial_value=1000).run(prices, weights)
    assert all(isinstance(f, pl.DataFrame) for f in out.values())
    r = out["backtest_results"]
    assert r.columns == RESULT_COLUMNS
    assert r.schema["date"] == pl.String
    assert all(r.schema[c] == pl.Float64 for c in RESULT_COLUMNS[1:])
    assert r["portfolio_value"].to_list() == pytest.approx([1000, 1035, 1070])
    assert out["backtest_positions"].columns == ["date", "A", "B", "cash"]
    assert out["backtest_weights"].columns == ["date", "A", "B", "cash"]
    m = out["backtest_metrics"]
    assert m.schema == {"metric": pl.String, "value": pl.Float64}
    assert m["metric"].to_list() == METRICS
    assert metric(out, "num_trades") == 1.0


def test_output_follows_the_prices_frame():
    pl = pytest.importorskip("polars")
    prices = pl.from_dict(prices_ab().to_dict(orient="list"))
    out = HawkBacktester(initial_value=1000).run(prices, weights_ab())
    assert isinstance(out["backtest_results"], pl.DataFrame)


def test_columns_are_the_weight_assets_in_weight_order():
    prices = prices_ab().assign(C=[1.0, 1.0, 1.0])
    # B before A in the weights; C has prices but no weights; D has weights but no prices
    weights = pd.DataFrame({"date": ["2023-01-01"], "B": [0.3], "A": [0.5], "D": [0.1]})
    out = HawkBacktester(initial_value=1000).run(prices, weights)
    assert list(out["backtest_positions"].columns) == ["date", "B", "A", "D", "cash"]
    assert list(out["backtest_weights"].columns) == ["date", "B", "A", "D", "cash"]
    # D can't be bought without a price: its weight stays in cash, as before
    assert out["backtest_positions"]["D"].tolist() == [0, 0, 0]
    assert out["backtest_positions"]["cash"].iloc[0] == pytest.approx(200)


def test_int64_columns_and_pandas_string_dtypes():
    prices = pd.DataFrame({"date": pd.array(["2023-01-01", "2023-01-02"], dtype="string"), "A": [10, 12]})
    weights = pd.DataFrame({"date": ["2023-01-01"], "A": pd.array([1], dtype="Int64")})
    out = HawkBacktester(initial_value=100).run(prices, weights)
    assert out["backtest_results"]["portfolio_value"].tolist() == pytest.approx([100, 120])


def test_defaults_and_costs():
    out = HawkBacktester().run(prices_ab(), weights_ab())
    assert out["backtest_results"]["portfolio_value"].iloc[0] == 1_000_000
    assert metric(out, "cumulative_commission_cost") == 0
    # 10 bps on 800 traded, and IBKR Pro Fixed: A 50 shares, B 15 shares, $1.00 minimum each
    out = HawkBacktester(1000, slippage_bps=10, fee_model="ibkr_pro_fixed").run(prices_ab(), weights_ab())
    assert metric(out, "cumulative_slippage_cost") == pytest.approx(0.8)
    assert metric(out, "cumulative_commission_cost") == pytest.approx(2.0)
    assert out["backtest_positions"]["cash"].iloc[0] == pytest.approx(200 - 0.8 - 2.0)
    # negative slippage is no slippage
    out = HawkBacktester(1000, slippage_bps=-5).run(prices_ab(), weights_ab())
    assert metric(out, "cumulative_slippage_cost") == 0


def test_metrics_frame_simulation_statistics():
    prices = pd.DataFrame({"date": [f"2023-01-{d:02d}" for d in range(1, 11)], "A": np.linspace(10, 11, 10)})
    weights = pd.DataFrame({"date": ["2023-01-03", "2023-01-06"], "A": [1.0, 0.5]})
    out = HawkBacktester(1000).run(prices, weights)
    assert len(out["backtest_results"]) == 8  # from the first weight date
    assert metric(out, "num_price_points") == 10  # every price row read
    assert metric(out, "num_weight_events") == 2
    assert metric(out, "num_trades") == 2
    for name in ("parsing_time_ms", "simulation_time_ms", "total_time_ms"):
        value = metric(out, name)
        assert value >= 0 and value == int(value)
    assert metric(out, "simulation_speed_dates_per_sec") > 0


# ── dates ─────────────────────────────────────────────────────────────


def test_date_formats_and_sorting():
    prices = pd.DataFrame(
        {"date": ["2023/1/18", "2023-01-15", "2023/01/16", "2023-1-17"], "A": [13.0, 10.0, 11.0, 12.0]}
    )
    weights = pd.DataFrame({"date": ["2023/1/15"], "A": [1.0]})
    out = HawkBacktester(100).run(prices, weights)
    r = out["backtest_results"]
    assert list(r["date"]) == ["2023-01-15", "2023-01-16", "2023-01-17", "2023-01-18"]
    assert r["portfolio_value"].tolist() == pytest.approx([100, 110, 120, 130])


@pytest.mark.parametrize(
    "date, message",
    [
        ("2023.01.15", "Invalid date format at row 2: 2023.01.15"),
        ("01-15-2023", "Invalid day at row 2: 2023"),
        ("2023-13-01", "Invalid date format at row 2: 2023-13-01"),
        ("2023-01-32", "Invalid date format at row 2: 2023-01-32"),
        ("not-a-date", "Invalid month at row 2: a"),
        ("2023-02-29", "Invalid date format at row 2: 2023-02-29"),
    ],
)
def test_bad_dates(date, message):
    prices = pd.DataFrame({"date": ["2023-01-01", date], "A": [1.0, 2.0]})
    with pytest.raises(ValueError) as e:
        HawkBacktester().run(prices, weights_ab())
    assert str(e.value) == "Error parsing price data: " + message


def test_bad_weight_dates_name_the_weights():
    weights = pd.DataFrame({"date": ["2023-1-1", "2023/13/1"], "A": [1.0, 0.0]})
    with pytest.raises(ValueError, match=r"^Error parsing weight data: Invalid date format at row 2: 2023/13/1$"):
        HawkBacktester().run(prices_ab(), weights)


# ── errors ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "frame, message",
    [
        (pd.DataFrame({"day": ["2023-01-01"], "A": [1.0]}), 'no column named "date"'),
        (pd.DataFrame({"date": pd.to_datetime(["2023-01-01"]), "A": [1.0]}), "Date column must be string type"),
        (pd.DataFrame({"date": ["2023-01-01"]}), "DataFrame must have at least one price column besides date"),
        (pd.DataFrame({"date": ["2023-01-01"], "A": ["1.0"]}), "Column A must be numeric type"),
        (
            pd.DataFrame({"date": ["2023-01-01"], "A": np.array([1.0], dtype=np.float32)}),
            "Column A must be numeric type",
        ),
        (pd.DataFrame({"date": ["2023-01-01"], "A": [True]}), "Column A must be numeric type"),
        (pd.DataFrame({"date": ["2023-01-01", "2023-01-02"], "A": [1.0, np.nan]}), "DataFrame contains null values"),
        (pd.DataFrame({"date": ["2023-01-01", None], "A": [1.0, 2.0]}), "DataFrame contains null values"),
    ],
)
def test_bad_price_frames(frame, message):
    with pytest.raises(ValueError) as e:
        HawkBacktester().run(frame, weights_ab())
    assert str(e.value) == "Error parsing price data: " + message


def test_bad_weight_frame_message():
    weights = pd.DataFrame({"date": ["2023-01-01"]})
    with pytest.raises(ValueError, match="^Error parsing weight data: DataFrame must have at least one weight column"):
        HawkBacktester().run(prices_ab(), weights)


def test_polars_frame_errors():
    pl = pytest.importorskip("polars")
    weights = pl.DataFrame({"date": ["2023-01-01"], "A": [1.0]})
    cases = [
        (
            pl.DataFrame({"date": ["2023-01-01"], "A": [1.0]}).with_columns(pl.col("date").str.to_date()),
            "Date column must be string type",
        ),
        (
            pl.DataFrame({"date": ["2023-01-01"], "A": [1]}, schema={"date": pl.String, "A": pl.Int32}),
            "Column A must be numeric type",
        ),
        (
            pl.DataFrame({"date": ["2023-01-01"], "A": [1.0]}, schema={"date": pl.String, "A": pl.Float32}),
            "Column A must be numeric type",
        ),
        (pl.DataFrame({"date": ["2023-01-01", "2023-01-02"], "A": [1.0, None]}), "DataFrame contains null values"),
        # NaN is not null to polars; the Rust backtester ran with it and every later value came out NaN
        (
            pl.DataFrame({"date": ["2023-01-01", "2023-01-02"], "A": [1.0, float("nan")]}),
            "Column A has a NaN price on 2023-01-02",
        ),
    ]
    for frame, message in cases:
        with pytest.raises(ValueError) as e:
            HawkBacktester().run(frame, weights)
        assert str(e.value) == "Error parsing price data: " + message
    # Int64 is fine
    out = HawkBacktester(10).run(pl.DataFrame({"date": ["2023-01-01"], "A": [3]}), weights)
    assert out["backtest_results"]["portfolio_value"].to_list() == [10.0]


def test_polars_nan_the_backtest_never_reads_is_fine():
    pl = pytest.importorskip("polars")
    nan = float("nan")
    weights = pl.DataFrame({"date": ["2023-01-02"], "A": [1.0]})
    # A before the first weight date, and B, which no weight names: Rust never read these
    prices = pl.DataFrame({"date": ["2023-01-01", "2023-01-02", "2023-01-03"], "A": [nan, 10.0, 11.0], "B": [nan] * 3})
    filled = prices.with_columns(pl.col("A").fill_nan(7.0), pl.col("B").fill_nan(1.0))
    out = HawkBacktester(1000).run(prices, weights)
    same = HawkBacktester(1000).run(filled, weights)
    assert out["backtest_results"]["portfolio_value"].to_list() == pytest.approx([1000, 1100])
    assert out["backtest_results"].equals(same["backtest_results"])
    assert out["backtest_positions"].equals(same["backtest_positions"])


def test_polars_nan_the_backtest_would_use_is_an_error():
    pl = pytest.importorskip("polars")
    nan = float("nan")
    prices = pl.DataFrame({"date": ["2023-01-03", "2023-01-01", "2023-01-02"], "A": [nan, 9.0, nan]})
    weights = pl.DataFrame({"date": ["2023-01-01"], "A": [1.0]})
    with pytest.raises(ValueError, match="^Error parsing price data: Column A has a NaN price on 2023-01-02$"):
        HawkBacktester().run(prices, weights)
    weights = pl.DataFrame({"date": ["2023-01-01", "2023-01-02"], "A": [1.0, nan]})
    with pytest.raises(ValueError, match="^Error parsing weight data: Column A has a NaN weight on 2023-01-02$"):
        HawkBacktester().run(prices.fill_nan(9.0), weights)


def test_inputs_that_are_not_dataframes_are_type_errors():
    pl = pytest.importorskip("polars")
    frame = pl.DataFrame({"date": ["2023-01-01"], "A": [1.0]})
    for prices, weights in [
        (frame.lazy(), frame),
        (frame, frame.lazy()),
        (frame["date"], frame),
        (prices_ab()["A"], weights_ab()),
        ({"date": ["2023-01-01"]}, weights_ab()),
    ]:
        with pytest.raises(TypeError, match="^expected a polars or pandas DataFrame"):
            HawkBacktester().run(prices, weights)


def test_no_weight_events():
    with pytest.raises(ValueError, match="^Cannot determine start date without weight events.$"):
        HawkBacktester().run(prices_ab(), weights_ab().iloc[0:0])


def test_an_asset_named_cash_is_a_runtime_error():
    weights = pd.DataFrame({"date": ["2023-01-01"], "A": [0.5], "cash": [0.1]})
    with pytest.raises(RuntimeError, match="^Error running backtest: "):
        HawkBacktester().run(prices_ab(), weights)


def test_constructor_errors():
    with pytest.raises(ValueError, match="unknown fee model 'ibkr'"):
        HawkBacktester(fee_model="ibkr")
    with pytest.raises(TypeError):
        HawkBacktester(fee_model=1)
    with pytest.raises(TypeError):
        HawkBacktester(initial_value="1000")
    with pytest.raises(TypeError):
        HawkBacktester().run({"date": ["2023-01-01"]}, weights_ab())


def test_polars_is_imported_only_for_polars_frames():
    code = (
        "import sys, pandas as pd\n"
        "from dv02_engine.backtest import HawkBacktester\n"
        "p = pd.DataFrame({'date': ['2023-01-01'], 'A': [1.0]})\n"
        "HawkBacktester().run(p, p)\n"
        "assert 'polars' not in sys.modules, 'polars was imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ── the numbers ───────────────────────────────────────────────────────


def test_rust_expected_values_by_hand():
    """A few of the Rust tests' expected values, worked out here from first principles."""
    days = [f"2023-01-0{d}" for d in range(1, 5)]
    # two rebalances: 700 + 300 cash, A 10 -> 12: 840 + 300 = 1140, half to A: 570 + 570, A 12 -> 11
    out = HawkBacktester(1000).run(
        pd.DataFrame({"date": days, "A": [10.0, 10.0, 12.0, 11.0]}),
        pd.DataFrame({"date": [days[0], days[2]], "A": [0.7, 0.5]}),
    )
    assert out["backtest_results"]["portfolio_value"].iloc[3] == pytest.approx(570 * 11 / 12 + 570)
    # drawdown 1100 -> 900
    out = HawkBacktester(1000).run(
        pd.DataFrame({"date": days, "A": [10.0, 11.0, 9.0, 10.0]}), pd.DataFrame({"date": [days[0]], "A": [1.0]})
    )
    assert metric(out, "max_drawdown") == pytest.approx(900 / 1100 - 1)
    # switching SPY to QQQ under IBKR Pro Fixed: 500 SPY bought, 500 sold at 105, 0.5 * 102,497.50 of QQQ at 205
    out = HawkBacktester(100_000, fee_model="ibkr_pro_fixed").run(
        pd.DataFrame({"date": days[:3], "SPY": [100.0, 105.0, 110.0], "QQQ": [200.0, 205.0, 210.0]}),
        pd.DataFrame({"date": days[:2], "SPY": [0.5, 0.0], "QQQ": [0.0, 0.5]}),
    )
    value_day2 = 52_500 + 50_000 - 2.5
    expected = 2.5 + 2.5 + max(0.5 * value_day2 / 205 * 0.005, 1.0)
    assert metric(out, "cumulative_commission_cost") == pytest.approx(expected)
    assert 6 < expected < 7
    # a 50% short: -500 -> -450 -> -400, cash 1500
    out = HawkBacktester(1000).run(
        pd.DataFrame({"date": days[:3], "A": [100.0, 90.0, 80.0]}), pd.DataFrame({"date": [days[0]], "A": [-0.5]})
    )
    assert out["backtest_results"]["portfolio_value"].tolist() == pytest.approx([1000, 1050, 1100])


def reference(prices, weights, initial_value, slippage_bps, ibkr):
    """The Rust backtester (backtester.rs and metrics.rs), re-done in plain Python from its source,
    except that years are the simulated days / 252, as in the port (Rust divided every price row).
    prices and weights are lists of (date, {asset: value}) in date order."""

    def commission(value, price):
        if value <= 0 or price <= 0:
            return 0.0
        return min(max(value / price * 0.005, 1.0), value * 0.01)

    tracked = list(dict.fromkeys(a for _, w in weights for a in w))
    held, cash = {}, initial_value  # asset -> [allocated, last_price]
    last = peak = initial_value
    i, trades = 0, 0
    rows = []
    start = weights[0][0]
    for date, px in prices:
        if date < start:
            continue
        for a, pos in held.items():
            if a in px:
                pos[0] *= px[a] / pos[1]
                pos[1] = px[a]
        volume = slip = fee = 0.0
        if i < len(weights) and date >= weights[i][0]:
            event = weights[i][1]
            total = cash + sum(p[0] for p in held.values())
            for a in set(held) | set(event):
                delta = abs(event.get(a, 0.0) * total - (held[a][0] if a in held else 0.0))
                volume += delta
                if slippage_bps > 0 and total > 0:
                    slip += delta * slippage_bps / 10_000
                if ibkr and delta > 0 and a in px:
                    fee += commission(delta, px[a])
            held = {a: [w * total, px[a]] for a, w in event.items() if a in px}
            cash = total * (1 - sum(w for a, w in event.items() if a in px)) - slip - fee
            i, trades = i + 1, trades + 1
        value = cash + sum(p[0] for p in held.values())
        peak = max(peak, value)
        rows.append(
            dict(
                portfolio_value=value,
                daily_return=value / last - 1,
                cumulative_return=value / initial_value - 1,
                drawdown=value / peak - 1,
                volume_traded=volume,
                daily_slippage_cost=slip,
                daily_commission_cost=fee,
                positions={a: held[a][0] if a in held else 0.0 for a in tracked},
                cash=cash,
            )
        )
        last = value
    r = [row["daily_return"] for row in rows]
    n, years = len(r), len(rows) / 252
    total_return = math.prod(1 + x for x in r) - 1
    annualized = (1 + total_return) ** (1 / years) - 1
    mean = sum(r) / n
    vol = math.sqrt(sum((x - mean) ** 2 for x in r) / (n - 1)) * math.sqrt(252)
    losses = [x for x in r if x < 0]
    downside = math.sqrt(sum(x * x for x in losses) / len(losses) * 252)
    book = sum(row["portfolio_value"] for row in rows) / n
    turnover = sum(row["volume_traded"] for row in rows) / years / (2 * book)
    metrics = dict(
        total_return=total_return,
        annualized_return=annualized,
        annualized_volatility=vol,
        sharpe_ratio=annualized / vol,
        sortino_ratio=annualized / downside,
        max_drawdown=min(0.0, *(row["drawdown"] for row in rows)),
        avg_drawdown=sum(row["drawdown"] for row in rows) / n,
        avg_daily_return=mean,
        win_rate=sum(x > 0 for x in r) / n,
        num_trades=trades,
        cumulative_volume_traded=sum(row["volume_traded"] for row in rows),
        portfolio_turnover=turnover,
        holding_period_years=1 / turnover,
        cumulative_slippage_cost=sum(row["daily_slippage_cost"] for row in rows),
        cumulative_commission_cost=sum(row["daily_commission_cost"] for row in rows),
    )
    return rows, metrics


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_matches_a_python_reference(seed):
    rng = np.random.default_rng(seed)
    assets = ["SPY", "TLT", "GLD", "IWM"]
    dates = pd.bdate_range("2022-01-03", periods=300).strftime("%Y-%m-%d").tolist()
    paths = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (len(dates), len(assets))), axis=0))
    prices = pd.DataFrame({"date": dates, **{a: paths[:, j] for j, a in enumerate(assets)}})
    # monthly-ish rebalances from the 10th price date, long/short, some days over 100% gross
    events = dates[10::21]
    w = rng.uniform(-0.5, 0.8, (len(events), len(assets)))
    weights = pd.DataFrame({"date": events, **{a: w[:, j] for j, a in enumerate(assets)}})

    out = HawkBacktester(1_000_000, slippage_bps=2.5, fee_model="ibkr_pro_fixed").run(prices, weights)
    rows, metrics = reference(
        [(d, dict(zip(assets, paths[i], strict=True))) for i, d in enumerate(dates)],
        [(d, dict(zip(assets, w[i], strict=True))) for i, d in enumerate(events)],
        1_000_000,
        2.5,
        True,
    )
    r = out["backtest_results"]
    assert len(r) == len(rows) == 290
    for col in (
        "portfolio_value",
        "daily_return",
        "cumulative_return",
        "drawdown",
        "volume_traded",
        "daily_slippage_cost",
        "daily_commission_cost",
    ):
        assert r[col].tolist() == pytest.approx([row[col] for row in rows], rel=1e-9, abs=1e-9), col
    p = out["backtest_positions"]
    for a in assets:
        assert p[a].tolist() == pytest.approx([row["positions"][a] for row in rows], rel=1e-9, abs=1e-6), a
    assert p["cash"].tolist() == pytest.approx([row["cash"] for row in rows], rel=1e-9, abs=1e-6)
    for name, value in metrics.items():
        assert metric(out, name) == pytest.approx(value, rel=1e-9), name
