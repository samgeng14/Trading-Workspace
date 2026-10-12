#include "dv02/backtest.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace dv02::backtest {
namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

void check_table(const Table& t, const char* what) {
    if (t.values.size() != t.rows() * t.cols())
        throw std::invalid_argument(std::string(what) + " have " + std::to_string(t.values.size()) + " values for " +
                                    std::to_string(t.rows()) + " dates x " + std::to_string(t.cols()) + " assets");
    if (!std::is_sorted(t.dates.begin(), t.dates.end()))
        throw std::invalid_argument(std::string(what) + " are not in date order");
    std::unordered_set<std::string_view> seen;
    for (const auto& a : t.assets)
        if (!seen.insert(a).second) throw std::invalid_argument(std::string(what) + " name " + a + " twice");
}

}  // namespace

FeeModel fee_model_from_string(std::string_view name) {
    if (name == "ibkr_pro_fixed") return FeeModel::IbkrProFixed;
    throw std::invalid_argument("unknown fee model '" + std::string(name) + "'; the one fee model is 'ibkr_pro_fixed'");
}

double ibkr_pro_fixed_commission(double trade_value, double price) {
    if (trade_value <= 0.0 || price <= 0.0) return 0.0;
    constexpr double kPerShare = 0.005, kMinPerOrder = 1.0, kMaxPerOrderFraction = 0.01;
    const double commission = std::fmax((trade_value / price) * kPerShare, kMinPerOrder);
    return std::fmin(commission, trade_value * kMaxPerOrderFraction);
}

void PortfolioState::update_positions(std::span<const double> prices) {
    for (std::size_t i = 0; i < positions.size() && i < prices.size(); ++i) {
        auto& pos = positions[i];
        if (!pos || std::isnan(prices[i])) continue;
        pos->allocated *= prices[i] / pos->last_price;
        pos->last_price = prices[i];
    }
}

double PortfolioState::total_value() const {
    double total = cash;
    for (const auto& pos : positions)
        if (pos) total += pos->allocated;
    return total;
}

Result run(const Table& prices, const Table& weights, const Settings& settings) {
    check_table(prices, "prices");
    check_table(weights, "weights");
    if (!settings.start_date && weights.rows() == 0)
        throw std::invalid_argument("Cannot determine start date without weight events.");
    const Date start = settings.start_date ? *settings.start_date : weights.dates.front();

    // One slot per asset: the weight table's assets first, in its order, then
    // any asset that only has prices.
    const std::size_t n_weighted = weights.cols();
    std::unordered_map<std::string_view, std::size_t> slot_of;
    for (std::size_t j = 0; j < n_weighted; ++j) slot_of.emplace(weights.assets[j], j);
    std::vector<std::size_t> price_slot(prices.cols());
    std::size_t n_assets = n_weighted;
    for (std::size_t j = 0; j < prices.cols(); ++j) {
        const auto [it, added] = slot_of.emplace(prices.assets[j], n_assets);
        if (added) ++n_assets;
        price_slot[j] = it->second;
    }

    Result out;
    // an asset gets position and weight columns once any event names it
    std::vector<std::size_t> tracked;
    for (std::size_t j = 0; j < n_weighted; ++j) {
        for (std::size_t r = 0; r < weights.rows(); ++r) {
            if (!std::isnan(weights.at(r, j))) {
                tracked.push_back(j);
                out.assets.push_back(weights.assets[j]);
                break;
            }
        }
    }

    out.positions.reserve(prices.rows() * tracked.size());
    out.weights.reserve(prices.rows() * tracked.size());

    PortfolioState book{settings.initial_value, std::vector<std::optional<DollarPosition>>(n_assets)};
    std::vector<double> price(n_assets);   // today's, by slot; NaN when there is none
    std::vector<double> target(n_assets);  // the event's weights, by slot; NaN when the event leaves it out
    double last_value = settings.initial_value;
    double peak_value = settings.initial_value;
    double cumulative_volume = 0.0, cumulative_slippage = 0.0, cumulative_commission = 0.0;
    std::size_t num_trades = 0;

    std::size_t next_event = 0;
    while (next_event < weights.rows() && weights.dates[next_event] < start) ++next_event;

    for (std::size_t r = 0; r < prices.rows(); ++r) {
        const Date today = prices.dates[r];
        if (today < start) continue;

        std::fill(price.begin(), price.end(), kNaN);
        for (std::size_t j = 0; j < prices.cols(); ++j) price[price_slot[j]] = prices.at(r, j);
        book.update_positions(price);

        double volume = 0.0, slippage = 0.0, commission = 0.0;
        if (next_event < weights.rows() && today >= weights.dates[next_event]) {
            std::fill(target.begin(), target.end(), kNaN);
            for (std::size_t j = 0; j < n_weighted; ++j) target[j] = weights.at(next_event, j);
            const double total = book.total_value();

            // Volume and costs are charged on every asset held or named in the
            // event, from where it is to weight * value (weight 0 if left out).
            // Commission needs a price; volume and slippage don't.
            for (std::size_t i = 0; i < n_assets; ++i) {
                const bool held = book.positions[i].has_value();
                const bool named = !std::isnan(target[i]);
                if (!held && !named) continue;
                const double delta =
                    std::fabs((named ? target[i] : 0.0) * total - (held ? book.positions[i]->allocated : 0.0));
                volume += delta;
                if (settings.slippage_bps > 0.0 && total > 0.0) slippage += delta * (settings.slippage_bps / 10000.0);
                if (settings.fee_model == FeeModel::IbkrProFixed && delta > 0.0 && !std::isnan(price[i]))
                    commission += ibkr_pro_fixed_commission(delta, price[i]);
            }
            cumulative_volume += volume;
            cumulative_slippage += slippage;
            cumulative_commission += commission;

            // Rebuild the book: assets named and priced today get weight * value;
            // the rest, less the costs, is cash.
            double allocated_weight = 0.0;
            for (std::size_t i = 0; i < n_assets; ++i) {
                book.positions[i].reset();
                if (std::isnan(target[i]) || std::isnan(price[i])) continue;
                allocated_weight += target[i];
                book.positions[i] = DollarPosition{target[i] * total, price[i]};
            }
            book.cash = total * (1.0 - allocated_weight);
            book.cash -= slippage;
            book.cash -= commission;

            ++next_event;
            ++num_trades;
        }

        const double value = book.total_value();
        for (const std::size_t i : tracked) {
            const double allocated = book.positions[i] ? book.positions[i]->allocated : 0.0;
            out.positions.push_back(allocated);
            out.weights.push_back(value > 0.0 ? allocated / value : 0.0);
        }
        out.cash.push_back(book.cash);
        out.cash_weight.push_back(value > 0.0 ? book.cash / value : 1.0);

        peak_value = std::fmax(peak_value, value);
        out.dates.push_back(today);
        out.portfolio_value.push_back(value);
        out.daily_return.push_back(last_value > 0.0 ? value / last_value - 1.0 : 0.0);
        out.daily_log_return.push_back(last_value > 0.0 ? std::log(value / last_value) : 0.0);
        out.cumulative_return.push_back(settings.initial_value > 0.0 ? value / settings.initial_value - 1.0 : 0.0);
        out.cumulative_log_return.push_back(settings.initial_value > 0.0 ? std::log(value / settings.initial_value)
                                                                          : 0.0);
        out.drawdown.push_back(peak_value > 0.0 ? value / peak_value - 1.0 : 0.0);
        out.volume_traded.push_back(volume);
        out.daily_slippage_cost.push_back(slippage);
        out.daily_commission_cost.push_back(commission);
        last_value = value;
    }

    // The Rust backtester counted every price date here, including those
    // before the start date that it never simulated; that stretched the
    // annualized figures. Count the simulated days.
    out.metrics = calculate_metrics(out.daily_return, out.drawdown, out.dates.size(), num_trades, out.volume_traded,
                                    cumulative_volume, out.portfolio_value, out.daily_slippage_cost,
                                    cumulative_slippage, out.daily_commission_cost, cumulative_commission);
    return out;
}

}  // namespace dv02::backtest
