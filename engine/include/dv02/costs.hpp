// Trading and carrying costs: what the live book pays IBKR and the market.
//
// One set of functions for the backtester (dv02::backtest) and, later, the
// live engine, so research charges the costs the live book pays. Each
// function prices one trade, or one stretch of carry, for one instrument;
// nothing here knows about a portfolio. Dollar amounts are positive costs.
//
//   commission  IBKR Pro Fixed for US stocks and ETFs; a per-contract table,
//               keyed by root, for futures
//   spread      half the bid-ask spread, in bps of the dollars traded
//   impact      square-root model: k * sigma_daily * sqrt(traded / ADV), in
//               bps of the dollars traded
//   borrow      an annual fee on short stock and ETF market value
//   interest    an annual rate earned on positive cash (none is charged on
//               negative cash: the mandate forbids it)
//   roll        futures: crossing the calendar spread plus both legs'
//               commission, on each roll date
//
// Borrow and interest accrue on calendar days, actual/360, as IBKR accrues
// USD balances.
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <span>
#include <string>
#include <string_view>

namespace dv02::costs {

// ── instruments ───────────────────────────────────────────────────────

enum class Kind {
    Equity,  // US stocks and ETFs: IBKR Pro Fixed, borrow when short
    Future,  // per-contract commission, rolls, no borrow, moves no cash
};

std::string_view to_string(Kind kind);

struct Instrument {
    Kind kind = Kind::Equity;
    std::string root{};       // futures: the key into CostModel::futures; empty means the symbol itself
    double multiplier = 1.0;  // futures: dollars per point (MES 5, ES 50, ZN 1000); 1 for equities
};

// Roll months as a bit set: bit m - 1 is month m (January is bit 0).
constexpr std::uint16_t month_bit(unsigned month) { return static_cast<std::uint16_t>(1u << (month - 1u)); }
inline constexpr std::uint16_t kQuarterlyHMUZ = month_bit(3) | month_bit(6) | month_bit(9) | month_bit(12);
inline constexpr std::uint16_t kQuarterlyGKQX = month_bit(2) | month_bit(5) | month_bit(8) | month_bit(11);
inline constexpr std::uint16_t kAllMonths = 0x0FFF;

struct FuturesRoot {
    // Dollars per contract per side, all in: IBKR's commission, the exchange
    // fee it passes on and the NFA fee.
    double commission = 0.0;
    // Crossing the calendar spread at a roll, in bps of |notional|. Both
    // legs' commission is charged on top.
    double roll_bps = 1.0;
    // A held contract rolls on the first price date in a roll month that is
    // on or after roll_day (1-31) and has a price for it.
    std::uint16_t roll_months = kQuarterlyHMUZ;
    unsigned roll_day = 10;
};

// IBKR Pro, up to 1,000 contracts a month, per side: IBKR's commission
// (USD 0.85 a contract; USD 0.25 for CME micro equity-index futures) plus the
// CME Globex non-member exchange fee IBKR passes on plus the USD 0.02 NFA fee.
//   ES, NQ, RTY      0.85 + 1.38 + 0.02 = 2.25  (IBKR's own worked ES example)
//   MES, MNQ, M2K    0.25 + 0.35 + 0.02 = 0.62
//   ZT, ZF           0.85 + 0.65 + 0.02 = 1.52
//   ZN, TN           0.85 + 0.80 + 0.02 = 1.67
//   ZB               0.85 + 0.87 + 0.02 = 1.74
// Equity-index futures roll on the 10th of Mar/Jun/Sep/Dec (the market rolls
// the Thursday 8 days before the third-Friday expiry, the 8th to the 14th);
// Treasury futures on the 24th of Feb/May/Aug/Nov, ahead of first notice day
// (the last business day of the month before delivery). Every roll costs
// 1 bp of notional for the calendar spread: conservative, since one tick of
// the spread is about 0.04 bp in ES and 0.4 bp in ZN.
// Exchange fees change; check IBKR's commission and fee pages before
// relying on these, and add any other root to CostModel::futures.
std::map<std::string, FuturesRoot, std::less<>> default_futures();

// ── one trade ─────────────────────────────────────────────────────────

// IBKR Pro Fixed for US stocks and ETFs: $0.005 a share, at least $1.00 an
// order, at most 1% of the trade's value (exchange and regulatory fees
// included; the SEC and FINRA fees on sales are not modelled). 0 when the
// trade value or the price isn't positive (a NaN isn't). Shares may be
// fractional.
double ibkr_pro_fixed(double trade_value, double price);

// Futures: contracts * per_contract, where contracts = trade_value / (price *
// multiplier), fractional (the backtester trades dollars, not whole
// contracts). 0 when any input isn't positive.
double futures_commission(double trade_value, double price, double multiplier, double per_contract);

// trade_value * half_spread_bps / 10,000, on the dollars traded, unsigned. 0
// when the trade value isn't positive (a NaN isn't).
double spread_cost(double trade_value, double half_spread_bps);

// Square-root market impact in bps of the dollars traded:
//   k * daily_vol * sqrt(trade_value / adv_value) * 10,000
// daily_vol is the standard deviation of daily returns (0.01 is 1%), and
// adv_value the average daily dollar volume. 0 when the trade value isn't
// positive; NaN when daily_vol or adv_value is missing (NaN), negative, or
// ADV is 0, so the caller can tell an unmeasured trade from a free one.
double impact_bps(double trade_value, double daily_vol, double adv_value, double k);

// trade_value * impact_bps(...) / 10,000, with the same 0 and the same NaN.
double impact_cost(double trade_value, double daily_vol, double adv_value, double k);

// The borrow fee on a short of |short_value| dollars held for `days` calendar
// days at annual_bps: |short_value| * annual_bps / 10,000 * days / 360. 0,
// never a refund, when the short value or the day count isn't positive (a NaN
// isn't).
double borrow_cost(double short_value, double annual_bps, double days);

// Interest on cash held for `days` calendar days at annual_bps:
// cash * annual_bps / 10,000 * days / 360. 0 when the cash or the day count
// isn't positive (a NaN isn't): negative cash earns and pays nothing here.
double cash_interest(double cash, double annual_bps, double days);

// Rolling a futures position of `notional` dollars (either sign) at `price`:
//   |notional| * roll_bps / 10,000 + 2 * contracts * per_contract
// with contracts = |notional| / (price * multiplier): the calendar spread
// plus closing one contract and opening the next. 0 when the notional is 0;
// the commission part is 0 when price or multiplier isn't positive.
double roll_cost(double notional, double price, double multiplier, double roll_bps, double per_contract);

// Whether a date (year, month 1-12, day 1-31) is in a roll month on or after
// the roll day. The backtester rolls once a roll month, on the first price
// date for which this holds.
bool in_roll_window(const FuturesRoot& root, unsigned month, unsigned day);

// ── market statistics ─────────────────────────────────────────────────

// The sample standard deviation of the daily returns between consecutive
// closes, closes[i] / closes[i - 1] - 1, counting only pairs where both are
// finite and positive. NaN with fewer than 2 such returns.
double daily_volatility(std::span<const double> closes);

// The mean of closes[i] * volumes[i] * multiplier over the days where the
// close is finite and positive and the volume finite and not negative.
// NaN when there are none. Volumes are shares, or contracts for futures.
double average_dollar_volume(std::span<const double> closes, std::span<const double> volumes, double multiplier);

// ── the model ─────────────────────────────────────────────────────────

using SymbolMap = std::map<std::string, double, std::less<>>;

struct CostModel {
    // IBKR Pro Fixed for equities, the futures table for futures.
    bool commissions = true;

    // Half the bid-ask spread, bps of the dollars traded: a default and
    // per-symbol overrides. 2 bps is conservative for SPY or QQQ (well under
    // 0.1 bp) and about right for the book's less traded ETFs.
    double half_spread_bps = 2.0;
    SymbolMap half_spread_bps_by_symbol{};

    // Square-root impact coefficient k (0 turns impact off). The literature
    // spans about 0.1 (Almgren et al. 2005, scheduled institutional
    // execution) to about 1 (Toth et al. 2011, metaorders); 0.5 sits between
    // and should be recalibrated from the fund's own fills.
    double impact_k = 0.5;
    // Price dates of history before the trade date used for daily
    // volatility and ADV. Neither uses the trade date itself.
    std::size_t impact_lookback = 20;

    // Annual borrow fee on short equity market value, bps: a default for
    // easy-to-borrow names (25 bps, IBKR's usual general-collateral level)
    // and per-symbol overrides for anything harder to borrow.
    double borrow_bps = 25.0;
    SymbolMap borrow_bps_by_symbol{};

    // Annual interest earned on positive cash, bps; 0 is off. The default is
    // what IBKR Pro pays on USD above USD 10,000: its benchmark rate less
    // 0.5%, 3.38% on IBKR's interest page in October 2026 (paper accounts
    // earn it too). Not modelled: the first USD 10,000 earns nothing, and a
    // NAV under USD 100,000 earns proportionally less. The rate moves with
    // the Fed: set the rate of the period backtested, or check the page.
    // Cash here excludes short-sale proceeds (conservative) and includes the
    // notional of futures, which move no cash.
    double cash_interest_bps = 338.0;

    // Futures costs by root.
    std::map<std::string, FuturesRoot, std::less<>> futures = default_futures();

    // Symbol -> instrument. A symbol not listed is an equity.
    std::map<std::string, Instrument, std::less<>> instruments{};

    double half_spread_for(std::string_view symbol) const;
    double borrow_for(std::string_view symbol) const;
    Instrument instrument_for(std::string_view symbol) const;
    // The futures costs for a futures instrument (by its root, or the symbol
    // when the root is empty). Throws std::invalid_argument when the table
    // has no such root.
    const FuturesRoot& futures_for(std::string_view symbol, const Instrument& instrument) const;
};

// Throws std::invalid_argument naming the first bad field: a negative or
// non-finite rate, an impact lookback under 2, a roll month set that is
// empty or a roll day outside 1-31, a multiplier that isn't positive (or
// isn't 1 on an equity), or a future whose root has no entry in futures.
void validate(const CostModel& model);

}  // namespace dv02::costs
