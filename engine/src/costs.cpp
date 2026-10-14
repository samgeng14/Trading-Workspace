// The cost model (include/dv02/costs.hpp).
#include "dv02/costs.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>

namespace dv02::costs {
namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
constexpr double kBps = 10'000.0;
constexpr double kDayCountBasis = 360.0;  // actual/360, as IBKR accrues USD

bool positive(double x) { return std::isfinite(x) && x > 0.0; }

void require(bool ok, const std::string& message) {
    if (!ok) throw std::invalid_argument(message);
}

void check_rate(double value, const std::string& name) {
    require(std::isfinite(value) && value >= 0.0,
            "CostModel." + name + " must be a finite number >= 0, not " + std::to_string(value));
}

}  // namespace

std::string_view to_string(Kind kind) { return kind == Kind::Future ? "future" : "equity"; }

std::map<std::string, FuturesRoot, std::less<>> default_futures() {
    constexpr double kStandard = 0.85 + 1.38 + 0.02, kMicro = 0.25 + 0.35 + 0.02;
    const auto index = [](double commission) { return FuturesRoot{commission, 1.0, kQuarterlyHMUZ, 10}; };
    const auto treasury = [](double commission) { return FuturesRoot{commission, 1.0, kQuarterlyGKQX, 24}; };
    return {
        {"ES", index(kStandard)},           {"NQ", index(kStandard)},           {"RTY", index(kStandard)},
        {"MES", index(kMicro)},             {"MNQ", index(kMicro)},             {"M2K", index(kMicro)},
        {"ZT", treasury(0.85 + 0.65 + 0.02)}, {"ZF", treasury(0.85 + 0.65 + 0.02)},
        {"ZN", treasury(0.85 + 0.80 + 0.02)}, {"TN", treasury(0.85 + 0.80 + 0.02)},
        {"ZB", treasury(0.85 + 0.87 + 0.02)},
    };
}

// ── one trade ─────────────────────────────────────────────────────────

double ibkr_pro_fixed(double trade_value, double price) {
    // The backtester's original arithmetic, operation for operation. The test
    // is "not positive" rather than "<= 0" so that a NaN trade is free: fmax
    // and fmin drop a NaN, which made it the $1.00 minimum.
    if (!(trade_value > 0.0) || !(price > 0.0)) return 0.0;
    constexpr double kPerShare = 0.005, kMinPerOrder = 1.0, kMaxPerOrderFraction = 0.01;
    const double commission = std::fmax((trade_value / price) * kPerShare, kMinPerOrder);
    return std::fmin(commission, trade_value * kMaxPerOrderFraction);
}

double futures_commission(double trade_value, double price, double multiplier, double per_contract) {
    if (!positive(trade_value) || !positive(price) || !positive(multiplier) || !positive(per_contract)) return 0.0;
    return trade_value / (price * multiplier) * per_contract;
}

double spread_cost(double trade_value, double half_spread_bps) {
    // the dollars traded are unsigned: a negative or NaN value is no trade, not a refund
    if (!(trade_value > 0.0)) return 0.0;
    return trade_value * half_spread_bps / kBps;
}

double impact_bps(double trade_value, double daily_vol, double adv_value, double k) {
    if (!(trade_value > 0.0)) return 0.0;
    if (!std::isfinite(daily_vol) || daily_vol < 0.0 || !positive(adv_value)) return kNaN;
    return k * daily_vol * std::sqrt(trade_value / adv_value) * kBps;
}

double impact_cost(double trade_value, double daily_vol, double adv_value, double k) {
    // no trade is free before the multiplication, so a NaN trade value is 0, not NaN
    if (!(trade_value > 0.0)) return 0.0;
    return trade_value * impact_bps(trade_value, daily_vol, adv_value, k) / kBps;
}

double borrow_cost(double short_value, double annual_bps, double days) {
    // no short, or a day count that isn't positive (a NaN isn't), is no carry: a
    // fee is never refunded
    const double size = std::fabs(short_value);
    if (!(size > 0.0) || !(days > 0.0)) return 0.0;
    return size * annual_bps / kBps * days / kDayCountBasis;
}

double cash_interest(double cash, double annual_bps, double days) {
    // cash that isn't positive (a NaN isn't) earns nothing, and pays nothing
    // here; nor does a day count that isn't positive
    if (!(cash > 0.0) || !(days > 0.0)) return 0.0;
    return cash * annual_bps / kBps * days / kDayCountBasis;
}

double roll_cost(double notional, double price, double multiplier, double roll_bps, double per_contract) {
    const double size = std::fabs(notional);
    if (!(size > 0.0)) return 0.0;
    const double spread = size * roll_bps / kBps;
    if (!positive(price) || !positive(multiplier)) return spread;
    return spread + 2.0 * (size / (price * multiplier)) * per_contract;
}

bool in_roll_window(const FuturesRoot& root, unsigned month, unsigned day) {
    return month >= 1 && month <= 12 && (root.roll_months & month_bit(month)) != 0 && day >= root.roll_day;
}

// ── market statistics ─────────────────────────────────────────────────

double daily_volatility(std::span<const double> closes) {
    // two passes over the returns: mean, then squared deviations
    double sum = 0.0;
    std::size_t n = 0;
    for (std::size_t i = 1; i < closes.size(); ++i) {
        if (positive(closes[i - 1]) && positive(closes[i])) {
            sum += closes[i] / closes[i - 1] - 1.0;
            ++n;
        }
    }
    if (n < 2) return kNaN;
    const double mean = sum / static_cast<double>(n);
    double squares = 0.0;
    for (std::size_t i = 1; i < closes.size(); ++i) {
        if (positive(closes[i - 1]) && positive(closes[i])) {
            const double r = closes[i] / closes[i - 1] - 1.0;
            squares += (r - mean) * (r - mean);
        }
    }
    return std::sqrt(squares / static_cast<double>(n - 1));
}

double average_dollar_volume(std::span<const double> closes, std::span<const double> volumes, double multiplier) {
    double sum = 0.0;
    std::size_t n = 0;
    for (std::size_t i = 0; i < closes.size() && i < volumes.size(); ++i) {
        if (!positive(closes[i]) || !std::isfinite(volumes[i]) || volumes[i] < 0.0) continue;
        sum += closes[i] * volumes[i] * multiplier;
        ++n;
    }
    return n ? sum / static_cast<double>(n) : kNaN;
}

// ── the model ─────────────────────────────────────────────────────────

double CostModel::half_spread_for(std::string_view symbol) const {
    const auto it = half_spread_bps_by_symbol.find(symbol);
    return it == half_spread_bps_by_symbol.end() ? half_spread_bps : it->second;
}

double CostModel::borrow_for(std::string_view symbol) const {
    const auto it = borrow_bps_by_symbol.find(symbol);
    return it == borrow_bps_by_symbol.end() ? borrow_bps : it->second;
}

Instrument CostModel::instrument_for(std::string_view symbol) const {
    const auto it = instruments.find(symbol);
    return it == instruments.end() ? Instrument{} : it->second;
}

const FuturesRoot& CostModel::futures_for(std::string_view symbol, const Instrument& instrument) const {
    const std::string_view root = instrument.root.empty() ? symbol : std::string_view(instrument.root);
    const auto it = futures.find(root);
    if (it == futures.end())
        throw std::invalid_argument("CostModel has no futures costs for root " + std::string(root) + " (symbol " +
                                    std::string(symbol) + "); add it to CostModel.futures");
    return it->second;
}

void validate(const CostModel& model) {
    check_rate(model.half_spread_bps, "half_spread_bps");
    for (const auto& [symbol, bps] : model.half_spread_bps_by_symbol) check_rate(bps, "half_spread_bps_by_symbol[" + symbol + "]");
    check_rate(model.impact_k, "impact_k");
    require(model.impact_lookback >= 2, "CostModel.impact_lookback must be at least 2 price dates, not " +
                                            std::to_string(model.impact_lookback));
    check_rate(model.borrow_bps, "borrow_bps");
    for (const auto& [symbol, bps] : model.borrow_bps_by_symbol) check_rate(bps, "borrow_bps_by_symbol[" + symbol + "]");
    check_rate(model.cash_interest_bps, "cash_interest_bps");
    for (const auto& [root, spec] : model.futures) {
        check_rate(spec.commission, "futures[" + root + "].commission");
        check_rate(spec.roll_bps, "futures[" + root + "].roll_bps");
        require(spec.roll_months != 0 && (spec.roll_months & ~kAllMonths) == 0,
                "CostModel.futures[" + root + "].roll_months must name at least one month, 1-12");
        require(spec.roll_day >= 1 && spec.roll_day <= 31,
                "CostModel.futures[" + root + "].roll_day must be 1-31, not " + std::to_string(spec.roll_day));
    }
    for (const auto& [symbol, instrument] : model.instruments) {
        require(positive(instrument.multiplier), "CostModel.instruments[" + symbol + "].multiplier must be positive");
        if (instrument.kind == Kind::Equity)
            require(instrument.multiplier == 1.0 && instrument.root.empty(),
                    "CostModel.instruments[" + symbol + "] is an equity: multiplier 1 and no root");
        else
            model.futures_for(symbol, instrument);  // throws when the root has no costs
    }
}

}  // namespace dv02::costs
