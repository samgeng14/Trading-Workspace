// Portfolio backtester: target weights on rebalance dates, prices every day.
//
// The book is kept in dollars per asset. Each price date marks every held
// position to that day's price; a position with no price that day keeps its
// last mark. On the first price date on or after a weight event's date the
// whole book is rebalanced: each asset in the event gets weight * value, the
// rest (1 - sum of weights) is cash, and slippage and commission come out of
// cash. One event is applied per price date, so a second event due the same
// day waits for the next price date.
//
// Weights may sum past 1 or go negative, with cash going negative to match:
// the backtester models what it is given. The fund's no-leverage mandate is
// enforced on real orders by dv02::risk, not here.
//
// This replaces the Rust hawk-backtester and gives the same numbers.
#pragma once

#include <chrono>
#include <cstddef>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace dv02::backtest {

using Date = std::chrono::sys_days;

// ── dates ─────────────────────────────────────────────────────────────

// Year, month and day separated by '-' or by '/': "2023-01-15", "2023/1/5".
// The year is four digits (a leading '+' is allowed); month and day may drop
// their leading zero. Throws std::invalid_argument naming the 1-based row,
// e.g. "Invalid date format at row 3: 2023.01.15".
Date parse_date(std::string_view text, std::size_t row);
std::vector<Date> parse_dates(const std::vector<std::string>& texts);  // row i + 1 in messages
std::string to_string(Date date);                                        // "2023-01-15"

// ── inputs ────────────────────────────────────────────────────────────

// A dates x assets table, row-major. NaN marks a missing entry: an asset with
// no price that day, or an asset left out of a weight event.
struct Table {
    std::vector<Date> dates;
    std::vector<std::string> assets;
    std::vector<double> values;

    std::size_t rows() const { return dates.size(); }
    std::size_t cols() const { return assets.size(); }
    double at(std::size_t row, std::size_t col) const { return values[row * cols() + col]; }
};

// The table with its rows in date order. Rows that share a date keep their order.
Table sort_by_date(Table table);

// A price or weight frame: dates as text (parsed as parse_date does), then sorted.
Table parse_table(const std::vector<std::string>& dates, std::vector<std::string> assets, std::vector<double> values);

// ── costs ─────────────────────────────────────────────────────────────

enum class FeeModel { None, IbkrProFixed };

// "ibkr_pro_fixed" -> IbkrProFixed. Any other name throws std::invalid_argument.
FeeModel fee_model_from_string(std::string_view name);

// IBKR Pro Fixed for U.S. stocks and ETFs: $0.005 a share, at least $1.00 an
// order, at most 1% of the trade's value. 0 when the trade value or the price
// isn't positive.
double ibkr_pro_fixed_commission(double trade_value, double price);

// ── the book ──────────────────────────────────────────────────────────

struct DollarPosition {
    double allocated = 0.0;   // marked-to-market dollars; negative is short
    double last_price = 0.0;  // the price it was last marked at
};

struct PortfolioState {
    double cash = 0.0;
    std::vector<std::optional<DollarPosition>> positions;  // one slot per asset; empty when not held

    // Marks each held position to its price: allocated *= price / last_price.
    // A position whose price is NaN, or past the end of prices, keeps its mark.
    void update_positions(std::span<const double> prices);
    double total_value() const;
};

// ── results ───────────────────────────────────────────────────────────

struct Metrics {
    double total_return = 0.0;
    double log_return = 0.0;  // ln(1 + sum of daily returns); not reported to Python
    double annualized_return = 0.0;
    double annualized_volatility = 0.0;
    double sharpe_ratio = 0.0;
    double sortino_ratio = 0.0;
    double max_drawdown = 0.0;
    double avg_drawdown = 0.0;
    double avg_daily_return = 0.0;
    double win_rate = 0.0;
    std::size_t num_trades = 0;
    std::vector<double> volume_traded;  // per day
    double cumulative_volume_traded = 0.0;
    double portfolio_turnover = 0.0;    // dollars traded a year / (2 x average portfolio value)
    double holding_period_years = 0.0;  // 1 / turnover; infinite with no turnover
    std::vector<double> daily_slippage_costs;
    double cumulative_slippage_cost = 0.0;
    std::vector<double> daily_commission_costs;
    double cumulative_commission_cost = 0.0;
};

// 252 trading days a year and a 0% risk-free rate. num_days sets the length
// of the backtest in years (num_days / 252) for the annualized figures.
Metrics calculate_metrics(std::span<const double> daily_returns, std::span<const double> drawdowns,
                          std::size_t num_days, std::size_t num_trades, std::vector<double> volume_traded,
                          double cumulative_volume_traded, std::span<const double> portfolio_values,
                          std::vector<double> daily_slippage_costs, double cumulative_slippage_cost,
                          std::vector<double> daily_commission_costs, double cumulative_commission_cost);

struct Result {
    // one row per simulated price date
    std::vector<Date> dates;
    std::vector<double> portfolio_value;
    std::vector<double> daily_return;
    std::vector<double> daily_log_return;
    std::vector<double> cumulative_return;
    std::vector<double> cumulative_log_return;
    std::vector<double> drawdown;       // value / running peak - 1
    std::vector<double> volume_traded;  // dollars bought plus dollars sold
    std::vector<double> daily_slippage_cost;
    std::vector<double> daily_commission_cost;

    // the assets named in any weight event, in weight-table order
    std::vector<std::string> assets;
    std::vector<double> positions;  // rows x assets, dollars
    std::vector<double> cash;
    std::vector<double> weights;  // rows x assets, fraction of portfolio value
    std::vector<double> cash_weight;

    Metrics metrics;
};

struct Settings {
    double initial_value = 1'000'000.0;
    std::optional<Date> start_date = std::nullopt;  // default: the first weight event's date
    double slippage_bps = 0.0;                      // 1.0 = 0.01% of every dollar traded
    FeeModel fee_model = FeeModel::None;
};

// Price dates before the start date are skipped, and so are weight events.
// Both tables must be in date order (parse_table and sort_by_date sort).
// Throws std::invalid_argument for a table out of order or of the wrong size,
// an asset named twice in one table, or no start date (none set and no
// weight events).
Result run(const Table& prices, const Table& weights, const Settings& settings = {});

}  // namespace dv02::backtest
