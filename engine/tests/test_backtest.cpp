// The backtester's simulation: marking, rebalancing, leverage, shorts, start
// date, volume and slippage (the Rust backtester_test.rs, case by case).
#include <cmath>
#include <numeric>
#include <stdexcept>
#include <string>

#include "backtest_helpers.hpp"
#include "harness.hpp"

using namespace dv02::backtest;
using namespace dv02::backtest::test;

TEST(bt_total_value) {
    // cash 100 and a position in A worth 200
    const PortfolioState portfolio{100.0, {DollarPosition{200.0, 10.0}}};
    CHECK_NEAR(portfolio.total_value(), 300.0, 1e-10);
}

TEST(bt_update_positions) {
    // A: 100 dollars at 10, then A trades at 12: 100 * 12/10 = 120
    PortfolioState portfolio{0.0, {DollarPosition{100.0, 10.0}}};
    portfolio.update_positions(std::vector<double>{12.0});
    CHECK_NEAR(portfolio.positions[0]->allocated, 120.0, 1e-10);
    CHECK_NEAR(portfolio.positions[0]->last_price, 12.0, 1e-10);
}

TEST(bt_no_weight_event) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 10.0}}}, {plus_days(now, 2), {{"A", 10.0}}}});
    const Result r = run(prices, Table{}, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK(r.dates.size() == 3);
    for (std::size_t i = 0; i < r.dates.size(); ++i) {
        CHECK_NEAR(r.portfolio_value[i], 1000.0, 1e-10);
        CHECK_NEAR(r.daily_return[i], 0.0, 1e-10);
        CHECK_NEAR(r.daily_log_return[i], 0.0, 1e-10);
        CHECK_NEAR(r.cumulative_return[i], 0.0, 1e-10);
        CHECK_NEAR(r.cash[i], 1000.0, 1e-10);
        CHECK_NEAR(r.cash_weight[i], 1.0, 1e-10);
    }
    // A is in no weight event, so it has no position or weight column
    CHECK(!has_column(r, "A"));
    CHECK(r.positions.empty() && r.weights.empty());
}

TEST(bt_with_weight_event) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}, {"B", 20.0}}},
                                {plus_days(now, 1), {{"A", 11.0}, {"B", 19.0}}},
                                {plus_days(now, 2), {{"A", 12.0}, {"B", 18.0}}}});
    const Table weights = table({{now, {{"A", 0.5}, {"B", 0.3}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // day 1
    CHECK_NEAR(r.portfolio_value[0], 1000.0, 1e-10);
    CHECK(r.cumulative_return[0] == 0.0);
    CHECK(r.cumulative_log_return[0] == 0.0);
    // day 2: 500 * 11/10 + 300 * 19/20 + 200 = 1035
    CHECK_NEAR(r.portfolio_value[1], 1035.0, 1e-10);
    CHECK_NEAR(r.daily_return[1], 0.035, 1e-3);
    CHECK_NEAR(r.cumulative_return[1], 0.035, 1e-3);
    CHECK_NEAR(r.cumulative_log_return[1], std::log(1.035), 1e-3);

    CHECK_NEAR(cell(r, r.positions, 0, "A"), 500.0, 1e-10);
    CHECK_NEAR(cell(r, r.positions, 0, "B"), 300.0, 1e-10);
    CHECK_NEAR(r.cash[0], 200.0, 1e-10);
    CHECK_NEAR(cell(r, r.weights, 0, "A"), 0.5, 1e-10);
    CHECK_NEAR(cell(r, r.weights, 0, "B"), 0.3, 1e-10);
    CHECK_NEAR(r.cash_weight[0], 0.2, 1e-10);
}

TEST(bt_multiple_weight_events) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}},
                                {plus_days(now, 1), {{"A", 10.0}}},
                                {plus_days(now, 2), {{"A", 12.0}}},
                                {plus_days(now, 3), {{"A", 11.0}}}});
    const Table weights = table({{now, {{"A", 0.7}}}, {plus_days(now, 2), {{"A", 0.5}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // 700 + 300 -> 840 + 300 = 1140 -> 570 + 570 -> 570 * 11/12 + 570 = 1092.5
    CHECK_NEAR(r.portfolio_value[3], 1092.5, 1e-1);
    const double day3_pos = cell(r, r.positions, 2, "A");
    const double day3_cash = r.cash[2];
    CHECK_NEAR(day3_pos / (day3_pos + day3_cash), 0.5, 1e-10);
    CHECK_NEAR(cell(r, r.weights, 0, "A"), 0.7, 1e-10);
    CHECK_NEAR(cell(r, r.weights, 2, "A"), 0.5, 1e-10);
}

TEST(bt_dataframe_output) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 100.0}}}, {plus_days(now, 1), {{"A", 101.0}}}});
    const Table weights = table({{now, {{"A", 0.8}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // every results column has a row per price date (the column names are
    // checked through Python, where the frames are built)
    const std::size_t n = prices.rows();
    CHECK(r.dates.size() == n && r.portfolio_value.size() == n && r.daily_return.size() == n &&
          r.daily_log_return.size() == n && r.cumulative_return.size() == n && r.cumulative_log_return.size() == n &&
          r.drawdown.size() == n && r.volume_traded.size() == n && r.daily_slippage_cost.size() == n &&
          r.daily_commission_cost.size() == n);
    // positions and weights: date, A (from the weight events) and cash
    CHECK(r.assets == std::vector<std::string>{"A"});
    CHECK(r.positions.size() == n && r.cash.size() == n);
    CHECK(r.weights.size() == n && r.cash_weight.size() == n);
}

TEST(bt_empty_portfolio) {
    PortfolioState portfolio;
    CHECK(portfolio.total_value() == 0.0);
    portfolio.update_positions({});
    CHECK(portfolio.total_value() == 0.0);
}

TEST(bt_portfolio_with_missing_price_updates) {
    PortfolioState portfolio{50.0, {DollarPosition{100.0, 10.0}, DollarPosition{200.0, 20.0}}};
    // a price for A only
    portfolio.update_positions(std::vector<double>{12.0, kNaN});
    CHECK_NEAR(portfolio.positions[0]->allocated, 120.0, 1e-10);  // 100 * 12/10
    CHECK_NEAR(portfolio.positions[1]->allocated, 200.0, 1e-10);  // unchanged
}

TEST(bt_zero_initial_value) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}});
    const Table weights = table({{now, {{"A", 0.8}}}});
    const Result r = run(prices, weights, {.initial_value = 0.0, .start_date = prices.dates[0]});

    for (std::size_t i = 0; i < r.dates.size(); ++i) {
        CHECK(r.portfolio_value[i] == 0.0);
        CHECK(r.daily_return[i] == 0.0);
        CHECK(r.cumulative_return[i] == 0.0);
        CHECK(cell(r, r.positions, i, "A") == 0.0);
        CHECK(r.cash[i] == 0.0);
        // with nothing to weigh, the weight is all cash
        CHECK(cell(r, r.weights, i, "A") == 0.0);
        CHECK(r.cash_weight[i] == 1.0);
    }
}

TEST(bt_missing_prices) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}, {"B", 20.0}}},
                                {plus_days(now, 1), {{"A", 11.0}}},    // B missing
                                {plus_days(now, 2), {{"B", 22.0}}}});  // A missing
    const Table weights = table({{now, {{"A", 0.4}, {"B", 0.4}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK(r.dates.size() == 3);
    CHECK(r.positions.size() == 3 * 2 && r.weights.size() == 3 * 2);
    CHECK(has_column(r, "A") && has_column(r, "B"));
}

TEST(bt_weight_event_with_invalid_asset) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}});
    const Table weights = table({{now, {{"A", 0.5}, {"B", 0.3}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // B has no prices but is in a weight event, so it is tracked
    CHECK(has_column(r, "A") && has_column(r, "B"));
}

TEST(bt_two_events_on_one_date_take_two_price_dates) {
    // The Rust tests left this case commented out ("only pass one weight event
    // per day"). What the backtester does: one event per price date, so the
    // second waits for the next price date.
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}});
    const Table weights = table({{now, {{"A", 0.5}}}, {now, {{"A", 0.8}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK_NEAR(cell(r, r.weights, 0, "A"), 0.5, 1e-12);
    // day 2: 500 * 11/10 + 500 = 1050, rebalanced to 80% A
    CHECK_NEAR(r.portfolio_value[1], 1050.0, 1e-9);
    CHECK_NEAR(cell(r, r.weights, 1, "A"), 0.8, 1e-12);
    CHECK(r.metrics.num_trades == 2);
}

TEST(bt_leveraged_positions) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}});
    const Table weights = table({{now, {{"A", 1.2}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK_NEAR(r.portfolio_value[0], 1000.0, 1e-10);
    CHECK_NEAR(cell(r, r.positions, 0, "A"), 1200.0, 1e-10);  // 1.2 * 1000
    CHECK_NEAR(r.cash[0], -200.0, 1e-10);                      // 1000 * (1 - 1.2)
}

TEST(bt_single_asset_high_leverage_negative_cash) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 100.0}}}, {plus_days(now, 1), {{"A", 105.0}}}});
    const Table weights = table({{now, {{"A", 2.0}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK_NEAR(cell(r, r.positions, 0, "A"), 2000.0, 1e-10);
    CHECK_NEAR(r.cash[0], -1000.0, 1e-10);
    CHECK_NEAR(r.portfolio_value[0], 1000.0, 1e-10);
}

TEST(bt_multiple_assets_combined_leverage_negative_cash) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 100.0}, {"B", 50.0}}}, {plus_days(now, 1), {{"A", 105.0}, {"B", 52.0}}}});
    const Table weights = table({{now, {{"A", 0.8}, {"B", 0.7}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK_NEAR(cell(r, r.positions, 0, "A"), 800.0, 1e-10);
    CHECK_NEAR(cell(r, r.positions, 0, "B"), 700.0, 1e-10);
    CHECK_NEAR(r.cash[0], -500.0, 1e-10);  // 1000 * (1 - 1.5)
    CHECK_NEAR(r.portfolio_value[0], 1000.0, 1e-10);
}

TEST(bt_mixed_long_short_leveraged_positions) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 100.0}, {"B", 50.0}}}, {plus_days(now, 1), {{"A", 95.0}, {"B", 55.0}}}});
    // long 1.5x A, short 0.2x B: net 1.3x
    const Table weights = table({{now, {{"A", 1.5}, {"B", -0.2}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK_NEAR(cell(r, r.positions, 0, "A"), 1500.0, 1e-10);
    CHECK_NEAR(cell(r, r.positions, 0, "B"), -200.0, 1e-10);
    CHECK_NEAR(r.cash[0], -300.0, 1e-10);  // 1000 * (1 - 1.3)
    CHECK_NEAR(r.portfolio_value[0], 1000.0, 1e-10);
}

TEST(bt_extreme_leverage_negative_cash) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 100.0}}}, {plus_days(now, 1), {{"A", 110.0}}}});
    const Table weights = table({{now, {{"A", 5.0}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK_NEAR(cell(r, r.positions, 0, "A"), 5000.0, 1e-10);
    CHECK_NEAR(r.cash[0], -4000.0, 1e-10);
    CHECK_NEAR(r.portfolio_value[0], 1000.0, 1e-10);
}

TEST(bt_short_position_returns) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 100.0}}},
                                {plus_days(now, 1), {{"A", 90.0}}},    // down 10%
                                {plus_days(now, 2), {{"A", 80.0}}}});  // down another 11.11%
    const Table weights = table({{now, {{"A", -0.5}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // -500 -> -450: +50 on 1000
    CHECK(r.daily_return[1] > 0.0);
    CHECK_NEAR(r.daily_return[1], 0.05, 1e-10);
    // -450 -> -400: +50 on 1050
    CHECK(r.daily_return[2] > 0.0);
    CHECK_NEAR(r.daily_return[2], 0.0476, 1e-3);
    // 1000 -> 1100
    CHECK(r.cumulative_return[2] > 0.0);
    CHECK_NEAR(r.cumulative_return[2], 0.10, 1e-3);
    CHECK_NEAR(r.portfolio_value[2], 1100.0, 1e-10);
}

TEST(bt_mixed_long_short_portfolio) {
    const Date now = today();
    const Table prices =
        table({{now, {{"LONG", 100.0}, {"SHORT", 100.0}}}, {plus_days(now, 1), {{"LONG", 110.0}, {"SHORT", 90.0}}}});
    const Table weights = table({{now, {{"LONG", 0.5}, {"SHORT", -0.3}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // +10% on 50% long, +10% on 30% short: +8%
    CHECK_NEAR(r.cumulative_return[1], 0.08, 1e-10);
    CHECK_NEAR(r.portfolio_value[1], 1080.0, 1e-10);
}

TEST(bt_respects_start_date) {
    const Date start = plus_days(today(), -3);
    const Table prices = table({{plus_days(start, -2), {{"A", 10.0}}},  // skipped
                                {plus_days(start, -1), {{"A", 11.0}}},  // skipped
                                {start, {{"A", 12.0}}},                 // first simulated date
                                {plus_days(start, 1), {{"A", 13.0}}},
                                {plus_days(start, 2), {{"A", 14.0}}}});
    const Table weights = table({{plus_days(start, -2), {{"A", 0.5}}}, {start, {{"A", 0.8}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = start});

    CHECK(r.dates.size() == 3);
    CHECK(r.cash.size() == 3 && r.cash_weight.size() == 3);
    CHECK(to_string(r.dates[0]) == to_string(start));
    CHECK_NEAR(cell(r, r.weights, 0, "A"), 0.8, 1e-10);
}

TEST(bt_volume_traded) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}, {"B", 20.0}}},
                                {plus_days(now, 1), {{"A", 11.0}, {"B", 21.0}}},
                                {plus_days(now, 2), {{"A", 12.0}, {"B", 22.0}}}});
    const Table weights = table({{now, {{"A", 0.5}, {"B", 0.3}}}, {plus_days(now, 2), {{"A", 0.3}, {"B", 0.5}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    CHECK(r.volume_traded[0] > 0.0);
    CHECK_NEAR(r.volume_traded[0], 800.0, 1e-10);  // 0.5 + 0.3 of 1000
    CHECK(r.volume_traded[1] == 0.0);              // no rebalance
    CHECK(r.volume_traded[2] > 0.0);
    CHECK(r.metrics.cumulative_volume_traded > 0.0);
    CHECK(r.metrics.cumulative_volume_traded ==
          std::accumulate(r.metrics.volume_traded.begin(), r.metrics.volume_traded.end(), 0.0));
}

TEST(bt_slippage_cost) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 10.0}}}});
    const Table weights = table({{now, {{"A", 1.0}}}});
    // 10 bps on 1000 traded: 1.0, paid out of cash
    const Result r =
        run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0], .slippage_bps = 10.0});

    CHECK_NEAR(r.portfolio_value[0], 999.0, 1e-10);
    CHECK_NEAR(r.portfolio_value[1], 999.0, 1e-10);
    CHECK_NEAR(r.cash[0], -1.0, 1e-10);
    CHECK_NEAR(r.cash[1], -1.0, 1e-10);
    CHECK_NEAR(r.daily_slippage_cost[0], 1.0, 1e-10);
    CHECK_NEAR(r.daily_slippage_cost[1], 0.0, 1e-10);
    CHECK_NEAR(r.metrics.cumulative_slippage_cost, 1.0, 1e-10);
}

// ── beyond the Rust tests ─────────────────────────────────────────────

TEST(bt_annualizes_over_the_simulated_days) {
    // 252 price dates before the first weight event and 252 from it: the
    // annualized return is over one year, not two
    const Date start = today();
    std::vector<std::pair<Date, Row>> rows;
    for (int i = -252; i < 252; ++i) rows.push_back({plus_days(start, i), {{"A", i < 0 ? 100.0 : 100.0 + i * 0.1}}});
    const Table prices = table(rows);
    const Table weights = table({{start, {{"A", 1.0}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0});

    CHECK(r.dates.size() == 252);
    CHECK_NEAR(r.metrics.total_return, 125.1 / 100.0 - 1.0, 1e-12);
    CHECK_NEAR(r.metrics.annualized_return, r.metrics.total_return, 1e-12);
}

TEST(bt_start_date_defaults_to_the_first_event) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}, {plus_days(now, 2), {{"A", 12.0}}}});
    const Table weights = table({{plus_days(now, 1), {{"A", 1.0}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0});
    CHECK(r.dates.size() == 2);
    CHECK(r.dates[0] == plus_days(now, 1));
    CHECK_NEAR(r.portfolio_value[1], 1000.0 * 12.0 / 11.0, 1e-9);
}

TEST(bt_run_rejects_bad_tables) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}});
    const Table weights = table({{now, {{"A", 1.0}}}});

    auto throws = [](auto&& fn) {
        try {
            fn();
        } catch (const std::invalid_argument&) {
            return true;
        }
        return false;
    };
    CHECK(throws([&] { run(prices, Table{}); }));  // no events, no start date
    Table unsorted = prices;
    std::swap(unsorted.dates[0], unsorted.dates[1]);
    CHECK(throws([&] { run(unsorted, weights); }));
    Table twice{{now}, {"A", "A"}, {0.5, 0.5}};
    CHECK(throws([&] { run(prices, twice); }));
    Table short_values{{now}, {"A", "B"}, {0.5}};
    CHECK(throws([&] { run(prices, short_values); }));
    CHECK(throws([&] { sort_by_date(short_values); }));
}
