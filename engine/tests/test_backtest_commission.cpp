// The IBKR Pro Fixed fee model in the backtester (the Rust commission_test.rs,
// case by case).
#include <stdexcept>

#include "backtest_helpers.hpp"
#include "harness.hpp"

using namespace dv02::backtest;
using namespace dv02::backtest::test;

namespace {

Settings ibkr(double initial_value, Date start, double slippage_bps = 0.0) {
    return {.initial_value = initial_value, .start_date = start, .slippage_bps = slippage_bps,
            .fee_model = FeeModel::IbkrProFixed};
}

}  // namespace

TEST(fee_ibkr_commission_minimum) {
    // 1% of 10,000 in SPY at $100: one share, $0.005, so the $1.00 minimum
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}}}, {plus_days(now, 1), {{"SPY", 100.0}}}});
    const Table weights = table({{now, {{"SPY", 0.01}}}});
    const Result r = run(prices, weights, ibkr(10'000.0, prices.dates[0]));

    CHECK(r.metrics.cumulative_commission_cost > 0.0);
    CHECK_NEAR(r.metrics.cumulative_commission_cost, 1.0, 1e-6);
    CHECK_NEAR(r.daily_commission_cost[0], 1.0, 1e-6);
}

TEST(fee_ibkr_commission_standard_rate) {
    // $50,000 at $50: 1,000 shares * $0.005 = $5.00, under the $500 cap
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 50.0}}}, {plus_days(now, 1), {{"SPY", 50.0}}}});
    const Table weights = table({{now, {{"SPY", 0.5}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    CHECK_NEAR(r.metrics.cumulative_commission_cost, 5.0, 1e-6);
}

TEST(fee_ibkr_commission_maximum_cap) {
    // $50,000 at $0.10: 500,000 shares * $0.005 = $2,500, capped at 1% = $500
    const Date now = today();
    const Table prices = table({{now, {{"PENNY", 0.10}}}, {plus_days(now, 1), {{"PENNY", 0.10}}}});
    const Table weights = table({{now, {{"PENNY", 0.5}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    CHECK_NEAR(r.metrics.cumulative_commission_cost, 500.0, 1e-6);
}

TEST(fee_ibkr_commission_multiple_trades) {
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}, {"QQQ", 200.0}}},
                                {plus_days(now, 1), {{"SPY", 105.0}, {"QQQ", 205.0}}},
                                {plus_days(now, 2), {{"SPY", 110.0}, {"QQQ", 210.0}}}});
    // buy 50% SPY ($2.50), then switch it to QQQ: sell 500 SPY ($2.50), buy ~250 QQQ (~$1.25)
    const Table weights = table({{now, {{"SPY", 0.5}}}, {plus_days(now, 1), {{"QQQ", 0.5}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    CHECK(r.metrics.cumulative_commission_cost > 6.0);
    CHECK(r.metrics.cumulative_commission_cost < 7.0);
}

TEST(fee_ibkr_commission_with_rebalancing) {
    const Date now = today();
    const Row px{{"SPY", 100.0}, {"QQQ", 200.0}, {"IWM", 150.0}};
    const Table prices = table({{now, px}, {plus_days(now, 1), px}, {plus_days(now, 2), px}});
    const Table weights = table({{now, {{"SPY", 0.4}, {"QQQ", 0.3}, {"IWM", 0.3}}},
                                 {plus_days(now, 1), {{"SPY", 0.3}, {"QQQ", 0.3}, {"IWM", 0.4}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    CHECK(r.metrics.cumulative_commission_cost > 0.0);
    CHECK(r.metrics.num_trades == 2);
}

TEST(fee_no_commission_without_fee_model) {
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}}}, {plus_days(now, 1), {{"SPY", 100.0}}}});
    const Table weights = table({{now, {{"SPY", 0.5}}}});
    const Result r = run(prices, weights, {.initial_value = 100'000.0, .start_date = prices.dates[0]});

    CHECK(r.metrics.cumulative_commission_cost == 0.0);
}

TEST(fee_commission_and_slippage_together) {
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}}}, {plus_days(now, 1), {{"SPY", 100.0}}}});
    const Table weights = table({{now, {{"SPY", 0.5}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0], 10.0));

    CHECK(r.metrics.cumulative_slippage_cost > 0.0);
    CHECK(r.metrics.cumulative_commission_cost > 0.0);
    CHECK_NEAR(r.metrics.cumulative_slippage_cost, 50.0, 1e-6);   // $50,000 * 0.1%
    CHECK_NEAR(r.metrics.cumulative_commission_cost, 2.5, 1e-6);  // 500 shares * $0.005
}

TEST(fee_commission_reduces_portfolio_value) {
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}}}, {plus_days(now, 1), {{"SPY", 100.0}}}});
    const Table weights = table({{now, {{"SPY", 0.5}}}});
    const Result without = run(prices, weights, {.initial_value = 100'000.0, .start_date = prices.dates[0]});
    const Result with = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    const double difference = without.portfolio_value.back() - with.portfolio_value.back();
    CHECK_NEAR(difference, with.metrics.cumulative_commission_cost, 1e-6);
}

TEST(fee_commission_on_short_positions) {
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}}}, {plus_days(now, 1), {{"SPY", 95.0}}}, {plus_days(now, 2), {{"SPY", 95.0}}}});
    const Table weights = table({{now, {{"SPY", -0.5}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    // $50,000 / $100 = 500 shares * $0.005
    CHECK_NEAR(r.metrics.cumulative_commission_cost, 2.5, 1e-6);
}

TEST(fee_commission_dataframe_column) {
    const Date now = today();
    const Table prices = table({{now, {{"SPY", 100.0}}}, {plus_days(now, 1), {{"SPY", 100.0}}}, {plus_days(now, 2), {{"SPY", 100.0}}}});
    const Table weights = table({{now, {{"SPY", 0.5}}}, {plus_days(now, 1), {{"SPY", 0.3}}}});
    const Result r = run(prices, weights, ibkr(100'000.0, prices.dates[0]));

    CHECK(r.daily_commission_cost.size() == 3);
    CHECK(r.daily_commission_cost[0] > 0.0);   // trade
    CHECK(r.daily_commission_cost[1] > 0.0);   // trade
    CHECK(r.daily_commission_cost[2] == 0.0);  // no trade
}

// ── beyond the Rust tests ─────────────────────────────────────────────

TEST(fee_ibkr_pro_fixed_schedule) {
    CHECK_NEAR(ibkr_pro_fixed_commission(50'000, 50), 5.0, 1e-12);      // per share
    CHECK_NEAR(ibkr_pro_fixed_commission(1'000, 100), 1.0, 1e-12);      // minimum
    CHECK_NEAR(ibkr_pro_fixed_commission(50'000, 0.10), 500.0, 1e-9);   // 1% cap
    CHECK_NEAR(ibkr_pro_fixed_commission(50, 10), 0.5, 1e-12);          // the cap wins over the minimum
    CHECK(ibkr_pro_fixed_commission(0, 10) == 0.0);
    CHECK(ibkr_pro_fixed_commission(100, 0) == 0.0);
}

TEST(fee_model_names) {
    CHECK(fee_model_from_string("ibkr_pro_fixed") == FeeModel::IbkrProFixed);
    bool threw = false;
    try {
        fee_model_from_string("ibkr_pro_tiered");
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    CHECK(threw);
}
