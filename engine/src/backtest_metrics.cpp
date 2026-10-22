// Summary metrics of a backtest, as the Rust hawk-backtester computed them.
#include <cmath>
#include <limits>
#include <numeric>
#include <utility>

#include "dv02/backtest.hpp"

namespace dv02::backtest {
namespace {

constexpr double kTradingDaysPerYear = 252.0;
constexpr double kRiskFreeRate = 0.0;

double sum(std::span<const double> xs) { return std::accumulate(xs.begin(), xs.end(), 0.0); }

}  // namespace

Metrics calculate_metrics(std::span<const double> daily_returns, std::span<const double> drawdowns,
                          std::size_t num_days, std::size_t num_trades, std::vector<double> volume_traded,
                          double cumulative_volume_traded, std::span<const double> portfolio_values,
                          std::vector<double> daily_slippage_costs, double cumulative_slippage_cost,
                          std::vector<double> daily_commission_costs, double cumulative_commission_cost) {
    constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
    constexpr double kInf = std::numeric_limits<double>::infinity();
    Metrics m;
    const auto n = static_cast<double>(daily_returns.size());

    m.total_return = std::accumulate(daily_returns.begin(), daily_returns.end(), 1.0,
                                     [](double acc, double r) { return acc * (1.0 + r); }) -
                     1.0;
    m.log_return = std::log(1.0 + sum(daily_returns));

    // compounded over the years the backtest covers; with no days that is
    // pow(1 + total, inf), which is 0% when nothing moved
    const double years = static_cast<double>(num_days) / kTradingDaysPerYear;
    m.annualized_return = std::pow(1.0 + m.total_return, years > 0.0 ? 1.0 / years : kInf) - 1.0;

    m.avg_daily_return = daily_returns.empty() ? 0.0 : sum(daily_returns) / n;
    // sample standard deviation; Sharpe divides the compounded annual return by it
    if (daily_returns.size() >= 2) {
        double squares = 0.0;
        for (const double r : daily_returns) squares += (r - m.avg_daily_return) * (r - m.avg_daily_return);
        m.annualized_volatility = std::sqrt(squares / (n - 1.0)) * std::sqrt(kTradingDaysPerYear);
        m.sharpe_ratio =
            m.annualized_volatility != 0.0 ? (m.annualized_return - kRiskFreeRate) / m.annualized_volatility : 0.0;
    }

    // turnover = dollars traded a year / (2 x average portfolio value)
    const double avg_portfolio_value =
        portfolio_values.empty() ? 1.0 : sum(portfolio_values) / static_cast<double>(portfolio_values.size());
    const double annualized_volume = years > 0.0 ? cumulative_volume_traded / years : 0.0;
    m.portfolio_turnover = avg_portfolio_value > 0.0 ? annualized_volume / (2.0 * avg_portfolio_value) : 0.0;
    m.holding_period_years = m.portfolio_turnover > 0.0 ? 1.0 / m.portfolio_turnover : kInf;

    // Sortino: the downside deviation is the root mean square of the losing days only
    double downside_squares = 0.0;
    std::size_t losing_days = 0;
    for (const double r : daily_returns) {
        if (r < 0.0) {
            downside_squares += r * r;
            ++losing_days;
        }
    }
    const double downside_variance = losing_days ? downside_squares / static_cast<double>(losing_days) : 0.0;
    const double downside_volatility = std::sqrt(downside_variance * kTradingDaysPerYear);
    m.sortino_ratio = downside_volatility != 0.0 ? (m.annualized_return - kRiskFreeRate) / downside_volatility : 0.0;

    // with no days these two are 0/0, NaN
    m.max_drawdown = std::accumulate(drawdowns.begin(), drawdowns.end(), 0.0,
                                     [](double acc, double d) { return std::fmin(acc, d); });
    m.avg_drawdown = drawdowns.empty() ? kNaN : sum(drawdowns) / static_cast<double>(drawdowns.size());
    std::size_t winning_days = 0;
    for (const double r : daily_returns)
        if (r > 0.0) ++winning_days;
    m.win_rate = daily_returns.empty() ? kNaN : static_cast<double>(winning_days) / n;

    m.num_trades = num_trades;
    m.volume_traded = std::move(volume_traded);
    m.cumulative_volume_traded = cumulative_volume_traded;
    m.daily_slippage_costs = std::move(daily_slippage_costs);
    m.cumulative_slippage_cost = cumulative_slippage_cost;
    m.daily_commission_costs = std::move(daily_commission_costs);
    m.cumulative_commission_cost = cumulative_commission_cost;
    return m;
}

}  // namespace dv02::backtest
