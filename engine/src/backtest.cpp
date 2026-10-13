#include "dv02/backtest.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace dv02::backtest {
namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
constexpr double kInf = std::numeric_limits<double>::infinity();
constexpr std::size_t kNone = static_cast<std::size_t>(-1);
constexpr double kMandateTolerance = 1e-9;  // as dv02::risk compares limits

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

// What the cost model needs to know about one asset, resolved once.
struct SlotCosts {
    costs::Kind kind = costs::Kind::Equity;
    double multiplier = 1.0;
    const costs::FuturesRoot* root = nullptr;  // futures only
    double half_spread_bps = 0.0;
    double borrow_bps = 0.0;
    int rolled_month = -1;  // year * 12 + month - 1 of the last roll date seen
};

// Daily volatility and average daily dollar volume of an asset over the
// lookback before a price row: the rows before it, never the row itself.
class MarketHistory {
public:
    MarketHistory(const Table& prices, const Table& volumes, std::size_t lookback, std::vector<std::size_t> price_col,
                  std::vector<std::size_t> volume_col)
        : prices_(prices),
          volumes_(volumes),
          lookback_(lookback),
          price_col_(std::move(price_col)),
          volume_col_(std::move(volume_col)),
          volume_row_(prices.rows(), kNone) {
        // the volume row with each price row's date (the last, if dates repeat)
        std::size_t v = 0;
        for (std::size_t r = 0; r < prices.rows(); ++r) {
            while (v < volumes.rows() && volumes.dates[v] < prices.dates[r]) ++v;
            for (std::size_t w = v; w < volumes.rows() && volumes.dates[w] == prices.dates[r]; ++w) volume_row_[r] = w;
        }
    }

    // the lookback's returns: closes on rows [row - lookback - 1, row - 1]
    double volatility(std::size_t slot, std::size_t row) {
        const std::size_t col = price_col_[slot];
        if (col == kNone) return kNaN;
        closes_.clear();
        for (std::size_t r = row > lookback_ ? row - lookback_ - 1 : 0; r < row; ++r) closes_.push_back(prices_.at(r, col));
        return costs::daily_volatility(closes_);
    }

    // rows [row - lookback, row - 1]
    double adv(std::size_t slot, std::size_t row, double multiplier) {
        const std::size_t col = price_col_[slot], vcol = volume_col_[slot];
        if (col == kNone || vcol == kNone) return kNaN;
        closes_.clear();
        shares_.clear();
        for (std::size_t r = row > lookback_ ? row - lookback_ : 0; r < row; ++r) {
            closes_.push_back(prices_.at(r, col));
            shares_.push_back(volume_row_[r] == kNone ? kNaN : volumes_.at(volume_row_[r], vcol));
        }
        return costs::average_dollar_volume(closes_, shares_, multiplier);
    }

private:
    const Table& prices_;
    const Table& volumes_;
    std::size_t lookback_;
    std::vector<std::size_t> price_col_;   // by slot; kNone when the asset has no prices
    std::vector<std::size_t> volume_col_;  // by slot; kNone when it has no volumes
    std::vector<std::size_t> volume_row_;  // by price row; kNone when that date has no volumes
    std::vector<double> closes_, shares_;  // scratch
};

}  // namespace

void validate(const Mandate& mandate) {
    if (!(std::isfinite(mandate.max_gross) && mandate.max_gross > 0.0))
        throw std::invalid_argument("Mandate.max_gross must be a positive number, not " +
                                    std::to_string(mandate.max_gross));
    if (!std::isfinite(mandate.min_cash))
        throw std::invalid_argument("Mandate.min_cash must be a finite number");
    for (const auto& [symbol, exposure] : mandate.exposure)
        if (!std::isfinite(exposure) || exposure == 0.0)
            throw std::invalid_argument("Mandate.exposure[" + symbol + "] must be a finite number other than 0");
}

FeeModel fee_model_from_string(std::string_view name) {
    if (name == "ibkr_pro_fixed") return FeeModel::IbkrProFixed;
    throw std::invalid_argument("unknown fee model '" + std::string(name) + "'; the one fee model is 'ibkr_pro_fixed'");
}

double ibkr_pro_fixed_commission(double trade_value, double price) { return costs::ibkr_pro_fixed(trade_value, price); }

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
    return run(prices, weights, Table{}, settings);
}

Result run(const Table& prices, const Table& weights, const Table& volumes, const Settings& settings) {
    check_table(prices, "prices");
    check_table(weights, "weights");
    check_table(volumes, "volumes");
    const costs::CostModel* const cm = settings.costs ? &*settings.costs : nullptr;
    if (cm) {
        if (settings.slippage_bps > 0.0 || settings.fee_model != FeeModel::None)
            throw std::invalid_argument("a cost model replaces slippage_bps and fee_model; set one or the other");
        costs::validate(*cm);
    } else if (volumes.rows() > 0 || volumes.cols() > 0) {
        throw std::invalid_argument("volumes are read only by a cost model; set Settings::costs");
    }
    const Mandate* const mandate = settings.mandate ? &*settings.mandate : nullptr;
    if (mandate) validate(*mandate);
    if (!settings.start_date && weights.rows() == 0)
        throw std::invalid_argument("Cannot determine start date without weight events.");
    const Date start = settings.start_date ? *settings.start_date : weights.dates.front();

    // One slot per asset: the weight table's assets first, in its order, then
    // any asset that only has prices.
    const std::size_t n_weighted = weights.cols();
    std::unordered_map<std::string_view, std::size_t> slot_of;
    std::vector<std::string_view> slot_name;
    for (std::size_t j = 0; j < n_weighted; ++j) {
        slot_of.emplace(weights.assets[j], j);
        slot_name.push_back(weights.assets[j]);
    }
    std::vector<std::size_t> price_slot(prices.cols());
    std::size_t n_assets = n_weighted;
    for (std::size_t j = 0; j < prices.cols(); ++j) {
        const auto [it, added] = slot_of.emplace(prices.assets[j], n_assets);
        if (added) {
            ++n_assets;
            slot_name.push_back(prices.assets[j]);
        }
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

    // The cost model's view of each asset, and the history it reads.
    std::vector<SlotCosts> slot_costs(cm ? n_assets : 0);
    std::optional<MarketHistory> history;
    if (cm) {
        for (std::size_t i = 0; i < n_assets; ++i) {
            const costs::Instrument instrument = cm->instrument_for(slot_name[i]);
            SlotCosts& sc = slot_costs[i];
            sc.kind = instrument.kind;
            sc.multiplier = instrument.multiplier;
            if (instrument.kind == costs::Kind::Future) sc.root = &cm->futures_for(slot_name[i], instrument);
            sc.half_spread_bps = cm->half_spread_for(slot_name[i]);
            sc.borrow_bps = cm->borrow_for(slot_name[i]);
        }
        std::vector<std::size_t> price_col(n_assets, kNone), volume_col(n_assets, kNone);
        for (std::size_t j = 0; j < prices.cols(); ++j) price_col[price_slot[j]] = j;
        for (std::size_t j = 0; j < volumes.cols(); ++j) {
            const auto it = slot_of.find(volumes.assets[j]);
            if (it != slot_of.end()) volume_col[it->second] = j;
        }
        history.emplace(prices, volumes, cm->impact_lookback, std::move(price_col), std::move(volume_col));
    }
    // The mandate's view: exposure per asset, and which assets move no cash.
    std::vector<double> exposure(mandate ? n_assets : 0, 1.0);
    std::vector<bool> moves_no_cash(n_assets, false);
    for (std::size_t i = 0; i < exposure.size(); ++i) {
        const auto it = mandate->exposure.find(slot_name[i]);
        if (it != mandate->exposure.end()) exposure[i] = it->second;
    }
    for (std::size_t i = 0; i < slot_costs.size(); ++i) moves_no_cash[i] = slot_costs[i].kind == costs::Kind::Future;

    PortfolioState book{settings.initial_value, std::vector<std::optional<DollarPosition>>(n_assets)};
    std::vector<double> price(n_assets);   // today's, by slot; NaN when there is none
    std::vector<double> target(n_assets);  // the event's weights, by slot; NaN when the event leaves it out
    double last_value = settings.initial_value;
    double peak_value = settings.initial_value;
    double cumulative_volume = 0.0, cumulative_slippage = 0.0, cumulative_commission = 0.0;
    std::size_t num_trades = 0;

    // carry: the previous price date's closing book, for borrow and interest
    std::optional<Date> carry_date;
    std::vector<double> carry_short(n_assets, 0.0);  // equity short market value, positive
    double carry_cash = 0.0;                         // cash that earns interest
    Metrics cost_totals;

    std::size_t next_event = 0;
    while (next_event < weights.rows() && weights.dates[next_event] < start) ++next_event;

    for (std::size_t r = 0; r < prices.rows(); ++r) {
        const Date today = prices.dates[r];
        if (today < start) continue;

        std::fill(price.begin(), price.end(), kNaN);
        for (std::size_t j = 0; j < prices.cols(); ++j) price[price_slot[j]] = prices.at(r, j);
        book.update_positions(price);

        // Carry since the last price date, then today's futures rolls.
        double borrow = 0.0, interest = 0.0, roll = 0.0;
        if (cm) {
            if (carry_date) {
                const auto days = static_cast<double>((today - *carry_date).count());
                for (std::size_t i = 0; i < n_assets; ++i)
                    if (carry_short[i] > 0.0) borrow += costs::borrow_cost(carry_short[i], slot_costs[i].borrow_bps, days);
                interest = costs::cash_interest(carry_cash, cm->cash_interest_bps, days);
            }
            const std::chrono::year_month_day ymd{today};
            const auto month = static_cast<unsigned>(ymd.month()), day = static_cast<unsigned>(ymd.day());
            const int month_key = static_cast<int>(ymd.year()) * 12 + static_cast<int>(month) - 1;
            for (std::size_t i = 0; i < n_assets; ++i) {
                SlotCosts& sc = slot_costs[i];
                if (!sc.root || std::isnan(price[i]) || sc.rolled_month == month_key ||
                    !costs::in_roll_window(*sc.root, month, day))
                    continue;
                sc.rolled_month = month_key;
                if (book.positions[i])
                    roll += costs::roll_cost(book.positions[i]->allocated, price[i], sc.multiplier, sc.root->roll_bps,
                                             cm->commissions ? sc.root->commission : 0.0);
            }
            book.cash += interest;
            book.cash -= borrow;
            book.cash -= roll;
        }

        double volume = 0.0, slippage = 0.0, commission = 0.0, spread = 0.0, impact = 0.0;
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
                if (cm) {
                    if (!(delta > 0.0)) continue;
                    const SlotCosts& sc = slot_costs[i];
                    if (cm->commissions && !std::isnan(price[i]))
                        commission += sc.root ? costs::futures_commission(delta, price[i], sc.multiplier,
                                                                          sc.root->commission)
                                              : costs::ibkr_pro_fixed(delta, price[i]);
                    spread += costs::spread_cost(delta, sc.half_spread_bps);
                    if (cm->impact_k > 0.0) {
                        const double cost = costs::impact_cost(delta, history->volatility(i, r),
                                                               history->adv(i, r, sc.multiplier), cm->impact_k);
                        if (std::isnan(cost))
                            ++cost_totals.unmeasured_impact_trades;
                        else
                            impact += cost;
                    }
                    continue;
                }
                if (settings.slippage_bps > 0.0 && total > 0.0) slippage += delta * (settings.slippage_bps / 10000.0);
                if (settings.fee_model == FeeModel::IbkrProFixed && delta > 0.0 && !std::isnan(price[i]))
                    commission += ibkr_pro_fixed_commission(delta, price[i]);
            }
            if (cm) slippage = spread + impact;
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

        // Futures notional: cash the book holds that a futures account would too.
        double futures_notional = 0.0;
        for (std::size_t i = 0; i < n_assets; ++i)
            if (moves_no_cash[i] && book.positions[i]) futures_notional += book.positions[i]->allocated;

        if (cm) {
            const double total_cost = commission + spread + impact + borrow + roll - interest;
            out.daily_spread_cost.push_back(spread);
            out.daily_impact_cost.push_back(impact);
            out.daily_borrow_cost.push_back(borrow);
            out.daily_roll_cost.push_back(roll);
            out.daily_interest_income.push_back(interest);
            out.daily_total_cost.push_back(total_cost);
            cost_totals.cumulative_spread_cost += spread;
            cost_totals.cumulative_impact_cost += impact;
            cost_totals.cumulative_borrow_cost += borrow;
            cost_totals.cumulative_roll_cost += roll;
            cost_totals.cumulative_interest_income += interest;
            cost_totals.cumulative_total_cost += total_cost;

            // tonight's book carries into the next price date
            double equity_short = 0.0;
            for (std::size_t i = 0; i < n_assets; ++i) {
                const auto& pos = book.positions[i];
                carry_short[i] = pos && !moves_no_cash[i] && pos->allocated < 0.0 ? -pos->allocated : 0.0;
                equity_short += carry_short[i];
            }
            carry_cash = book.cash + futures_notional - equity_short;
            carry_date = today;
        }

        if (mandate) {
            double gross = 0.0;
            for (std::size_t i = 0; i < n_assets; ++i)
                if (book.positions[i]) gross += std::fabs(book.positions[i]->allocated * exposure[i]);
            double gross_fraction = kNaN, cash_fraction = kNaN;
            bool breach = true;  // a value that isn't positive
            if (value > 0.0) {
                gross_fraction = gross / value;
                cash_fraction = (book.cash + futures_notional) / value;
                breach = gross_fraction > mandate->max_gross + kMandateTolerance ||
                         cash_fraction < mandate->min_cash - kMandateTolerance;
            }
            out.gross_exposure.push_back(gross_fraction);
            out.mandate_cash.push_back(cash_fraction);
            out.mandate_breach.push_back(breach ? 1 : 0);
        }
    }

    // The Rust backtester counted every price date here, including those
    // before the start date that it never simulated; that stretched the
    // annualized figures. Count the simulated days.
    out.metrics = calculate_metrics(out.daily_return, out.drawdown, out.dates.size(), num_trades, out.volume_traded,
                                    cumulative_volume, out.portfolio_value, out.daily_slippage_cost,
                                    cumulative_slippage, out.daily_commission_cost, cumulative_commission);
    Metrics& m = out.metrics;
    m.cumulative_spread_cost = cost_totals.cumulative_spread_cost;
    m.cumulative_impact_cost = cost_totals.cumulative_impact_cost;
    m.cumulative_borrow_cost = cost_totals.cumulative_borrow_cost;
    m.cumulative_roll_cost = cost_totals.cumulative_roll_cost;
    m.cumulative_interest_income = cost_totals.cumulative_interest_income;
    m.cumulative_total_cost = cost_totals.cumulative_total_cost;
    m.unmeasured_impact_trades = cost_totals.unmeasured_impact_trades;
    if (mandate) {
        double lowest_cash = kInf;
        for (std::size_t k = 0; k < out.mandate_breach.size(); ++k) {
            if (out.mandate_breach[k]) ++m.mandate_breach_days;
            if (!std::isnan(out.gross_exposure[k])) m.max_gross_exposure = std::fmax(m.max_gross_exposure, out.gross_exposure[k]);
            if (!std::isnan(out.mandate_cash[k])) lowest_cash = std::fmin(lowest_cash, out.mandate_cash[k]);
        }
        m.min_mandate_cash = std::isinf(lowest_cash) ? kNaN : lowest_cash;
    }
    return out;
}

}  // namespace dv02::backtest
