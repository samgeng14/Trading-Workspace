// Backtest metrics (the Rust metrics_test.rs, case by case), plus each metric
// against a hand computation.
#include <algorithm>
#include <cmath>
#include <numeric>
#include <vector>

#include "backtest_helpers.hpp"
#include "harness.hpp"

using namespace dv02::backtest;
using namespace dv02::backtest::test;

namespace {

std::vector<double> repeat(double v, std::size_t n) { return std::vector<double>(n, v); }

}  // namespace

TEST(mx_drawdown_calculation) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}},
                                {plus_days(now, 1), {{"A", 11.0}}},    // peak
                                {plus_days(now, 2), {{"A", 9.0}}},     // drawdown
                                {plus_days(now, 3), {{"A", 10.0}}}});  // recovery
    const Table weights = table({{now, {{"A", 1.0}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});

    // 1100 -> 900: -18.18%
    const double max_drawdown = std::accumulate(r.drawdown.begin(), r.drawdown.end(), 0.0,
                                                [](double acc, double d) { return std::min(acc, d); });
    CHECK_NEAR(max_drawdown, -0.1818, 1e-3);
}

TEST(mx_turnover_and_holding_period) {
    // one year, 1,000 traded on an average book of 1,000: 1000 / (2 x 1000) = 0.5, held 2 years
    Metrics m = calculate_metrics(std::vector<double>{0.0},             // daily returns
                                  std::vector<double>{0.0},             // drawdowns
                                  252,                                  // one year of trading days
                                  4,                                    // trades
                                  std::vector<double>{250, 250, 250, 250},  // volume traded each time
                                  1000.0,                               // total volume traded
                                  repeat(1000.0, 252),                  // portfolio value
                                  repeat(0.0, 252), 0.0,                // slippage
                                  repeat(0.0, 252), 0.0);               // commission
    CHECK_NEAR(m.portfolio_turnover, 0.5, 1e-10);
    CHECK_NEAR(m.holding_period_years, 2.0, 1e-10);

    // two years, 4,000 traded: 2,000 a year, turnover 1.0, held 1 year
    m = calculate_metrics(repeat(0.0, 504), repeat(0.0, 504), 504, 8, repeat(500.0, 8), 4000.0, repeat(1000.0, 504),
                          repeat(0.0, 504), 0.0, repeat(0.0, 504), 0.0);
    CHECK_NEAR(m.portfolio_turnover, 1.0, 1e-10);
    CHECK_NEAR(m.holding_period_years, 1.0, 1e-10);

    // no trading: no turnover, held forever
    m = calculate_metrics(repeat(0.0, 252), repeat(0.0, 252), 252, 0, {}, 0.0, repeat(1000.0, 252), repeat(0.0, 252),
                          0.0, repeat(0.0, 252), 0.0);
    CHECK(m.portfolio_turnover == 0.0);
    CHECK(std::isinf(m.holding_period_years));

    // weekly trading: 5,200 a year, turnover 2.6, held 1/2.6 years
    m = calculate_metrics(repeat(0.0, 252), repeat(0.0, 252), 252, 52, repeat(100.0, 52), 5200.0, repeat(1000.0, 252),
                          repeat(0.0, 252), 0.0, repeat(0.0, 252), 0.0);
    CHECK_NEAR(m.portfolio_turnover, 2.6, 1e-10);
    CHECK_NEAR(m.holding_period_years, 1.0 / 2.6, 1e-10);
}

TEST(mx_volume_tracking) {
    const std::vector<double> volume_trades{100.0, 200.0, 300.0, 400.0};
    const double total_volume = 1000.0;
    const Metrics m = calculate_metrics(repeat(0.0, 252), repeat(0.0, 252), 252, 4, volume_trades, total_volume,
                                        repeat(1000.0, 252), repeat(0.0, 252), 0.0, repeat(0.0, 252), 0.0);
    CHECK(m.volume_traded == volume_trades);
    CHECK(m.cumulative_volume_traded == total_volume);
    CHECK(std::accumulate(m.volume_traded.begin(), m.volume_traded.end(), 0.0) == total_volume);
}

TEST(mx_edge_cases) {
    // a zero portfolio value: no turnover
    Metrics m = calculate_metrics(std::vector<double>{0.0}, std::vector<double>{0.0}, 1, 1, std::vector<double>{100.0},
                                  100.0, std::vector<double>{0.0}, std::vector<double>{0.0}, 0.0,
                                  std::vector<double>{0.0}, 0.0);
    CHECK(m.portfolio_turnover == 0.0);
    CHECK(std::isinf(m.holding_period_years));

    // no days at all
    m = calculate_metrics({}, {}, 0, 0, {}, 0.0, {}, {}, 0.0, {}, 0.0);
    CHECK(m.portfolio_turnover == 0.0);
    CHECK(std::isinf(m.holding_period_years));
    // and what the rest come to: nothing moved, and the averages over no days are NaN
    CHECK(m.total_return == 0.0 && m.annualized_return == 0.0 && m.sharpe_ratio == 0.0 && m.sortino_ratio == 0.0);
    CHECK(m.max_drawdown == 0.0);
    CHECK(std::isnan(m.avg_drawdown) && std::isnan(m.win_rate));
}

// ── beyond the Rust tests ─────────────────────────────────────────────

TEST(mx_each_metric_by_hand) {
    const std::vector<double> r{0.01, -0.02, 0.03, 0.0, -0.01};
    const std::vector<double> dd{0.0, -0.02, 0.0, 0.0, -0.01};
    const Metrics m = calculate_metrics(r, dd, 5, 2, repeat(0.0, 5), 300.0, std::vector<double>{100, 101, 102, 103, 104},
                                        repeat(0.0, 5), 1.5, repeat(0.0, 5), 2.5);

    const double total = 1.01 * 0.98 * 1.03 * 1.0 * 0.99 - 1.0;
    CHECK_NEAR(m.total_return, total, 1e-15);
    CHECK_NEAR(m.annualized_return, std::pow(1.0 + total, 252.0 / 5.0) - 1.0, 1e-12);
    const double mean = 0.01 / 5.0;
    CHECK_NEAR(m.avg_daily_return, mean, 1e-15);
    double ss = 0.0;
    for (double x : r) ss += (x - mean) * (x - mean);
    const double vol = std::sqrt(ss / 4.0) * std::sqrt(252.0);
    CHECK_NEAR(m.annualized_volatility, vol, 1e-12);
    CHECK_NEAR(m.sharpe_ratio, m.annualized_return / vol, 1e-12);
    // downside: the two losing days only
    const double downside = std::sqrt((0.02 * 0.02 + 0.01 * 0.01) / 2.0 * 252.0);
    CHECK_NEAR(m.sortino_ratio, m.annualized_return / downside, 1e-12);
    CHECK_NEAR(m.max_drawdown, -0.02, 1e-15);
    CHECK_NEAR(m.avg_drawdown, -0.006, 1e-15);
    CHECK_NEAR(m.win_rate, 0.4, 1e-15);
    CHECK_NEAR(m.log_return, std::log(1.01), 1e-15);
    // 300 over 5/252 years on an average book of 102
    CHECK_NEAR(m.portfolio_turnover, 300.0 / (5.0 / 252.0) / (2.0 * 102.0), 1e-9);
    CHECK(m.num_trades == 2);
    CHECK(m.cumulative_slippage_cost == 1.5 && m.cumulative_commission_cost == 2.5);
}
