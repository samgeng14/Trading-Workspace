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
// enforced on real orders by dv02::risk; here it is an optional check that
// flags the days a backtest breaks it (Settings::mandate) and never rescales.
//
// Costs: with no cost model, slippage_bps and fee_model charge exactly what
// the Rust hawk-backtester charged, and the numbers are the same. A cost
// model (Settings::costs, dv02::costs) replaces both and charges what the
// live book pays: commission, spread, impact, borrow, cash interest, futures
// rolls. A day with a cost model runs in this order:
//   1. mark every held position to the day's price
//   2. accrue borrow and cash interest for the calendar days since the last
//      price date, on the previous day's closing book
//   3. roll held futures whose roll date this is
//   4. rebalance, if a weight event is due, paying commission, spread and
//      impact on each asset's dollars traded
// Every cost comes out of cash; interest goes into it.
//
// This replaces the Rust hawk-backtester and gives the same numbers.
#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "dv02/costs.hpp"

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

    // With a cost model (0 without one). Commission is cumulative_commission_cost
    // and spread + impact is cumulative_slippage_cost.
    double cumulative_spread_cost = 0.0;
    double cumulative_impact_cost = 0.0;
    double cumulative_borrow_cost = 0.0;
    double cumulative_roll_cost = 0.0;
    double cumulative_interest_income = 0.0;
    double cumulative_total_cost = 0.0;   // commission + spread + impact + borrow + roll - interest
    std::size_t unmeasured_impact_trades = 0;  // trades charged no impact: no volume, or too little history

    // With a mandate check (0 without one).
    std::size_t mandate_breach_days = 0;
    double max_gross_exposure = 0.0;  // the largest end-of-day gross, fraction of value
    double min_mandate_cash = 0.0;    // the lowest end-of-day cash as the mandate counts it, fraction of value
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
    std::vector<double> daily_slippage_cost;    // with a cost model: spread + impact
    std::vector<double> daily_commission_cost;

    // With a cost model, one per row (empty without one).
    std::vector<double> daily_spread_cost;
    std::vector<double> daily_impact_cost;
    std::vector<double> daily_borrow_cost;
    std::vector<double> daily_roll_cost;
    std::vector<double> daily_interest_income;
    std::vector<double> daily_total_cost;  // commission + spread + impact + borrow + roll - interest

    // With a mandate check, one per row at the day's close (empty without one).
    std::vector<double> gross_exposure;           // sum of |position x exposure| / value
    std::vector<double> mandate_cash;             // cash / value, futures moving no cash
    std::vector<std::uint8_t> mandate_breach;     // 1 when gross or cash is outside the mandate

    // the assets named in any weight event, in weight-table order
    std::vector<std::string> assets;
    std::vector<double> positions;  // rows x assets, dollars
    std::vector<double> cash;
    std::vector<double> weights;  // rows x assets, fraction of portfolio value
    std::vector<double> cash_weight;

    Metrics metrics;
};

// The no-leverage mandate as dv02::risk measures it, checked at each day's
// close: gross = sum of |position x exposure| / value <= max_gross, and
// cash / value >= min_cash, where cash counts futures as moving no cash
// (it is the book's cash plus the futures' notional). Futures are the cost
// model's futures instruments; without a cost model every asset is an
// equity. Limits are compared with a 1e-9 tolerance, as dv02::risk does. A
// value that isn't positive is a breach. Costs paid from a fully invested
// book take cash below 0, which this flags: leave a cash buffer.
struct Mandate {
    double max_gross = 1.0;
    double min_cash = 0.0;
    // Economic exposure per dollar of market value by symbol, default 1:
    // 3 for TQQQ, -1 for an inverse ETF (as dv02::risk::Position::exposure).
    std::map<std::string, double, std::less<>> exposure{};
};

// Throws std::invalid_argument for a max_gross that isn't positive, a
// min_cash that isn't finite, or an exposure that is 0 or not finite.
void validate(const Mandate& mandate);

struct Settings {
    double initial_value = 1'000'000.0;
    std::optional<Date> start_date = std::nullopt;  // default: the first weight event's date
    double slippage_bps = 0.0;                      // 1.0 = 0.01% of every dollar traded
    FeeModel fee_model = FeeModel::None;
    // The live book's costs, replacing slippage_bps and fee_model (setting
    // both is an error).
    std::optional<costs::CostModel> costs = std::nullopt;
    // Flag the days the book breaks the no-leverage mandate.
    std::optional<Mandate> mandate = std::nullopt;
};

// Price dates before the start date are skipped, and so are weight events;
// the cost model still reads their prices and volumes as history. All tables
// must be in date order (parse_table and sort_by_date sort). Throws
// std::invalid_argument for a table out of order or of the wrong size, an
// asset named twice in one table, no start date (none set and no weight
// events), a cost model set with slippage_bps or fee_model, an invalid cost
// model or mandate, or volumes without a cost model.
Result run(const Table& prices, const Table& weights, const Settings& settings = {});

// With daily volumes (shares, or contracts for futures) for the cost model's
// market impact. Volumes are matched to prices by date and asset; a missing
// volume, or an asset with none, leaves its trades without impact (counted in
// Metrics::unmeasured_impact_trades), paying spread and commission only.
Result run(const Table& prices, const Table& weights, const Table& volumes, const Settings& settings);

}  // namespace dv02::backtest
