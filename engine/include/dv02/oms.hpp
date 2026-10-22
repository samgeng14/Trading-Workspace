// The order manager's decisions: whether to rebalance, what to send, and in
// which auction.
//
// These are ports of the Python runner's, made so the engine can take over
// order handling one piece at a time (docs/PLAN.md §6, migration step 2, the
// shadow OMS). Python still sends the orders. Every run hands the engine the
// same inputs and records whether it would have sent the same orders
// (trading-engine/src/production/engine_shadow.py):
//
//   current_weights()    daily_runner._current_weights
//   rebalance_decision() src/runtime/auto_rebalance.decide_rebalance_with_drift
//   plan_basket()        build_paper_basket.build_reconciliation, and the
//                        sending order of submit_paper_orders.sells_first
//   execution_for()      src/broker/ibkr/auction.execution_for
//
// The ports keep Python's arithmetic step for step, down to the order of the
// additions in a sum (numpy's pairwise summation), so the same inputs give the
// same shares, not merely close ones. Their quirks are kept too and noted
// where they are: the comparison is only worth something if it is exact.
#pragma once

#include <cstdint>
#include <limits>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "dv02/nyse.hpp"

namespace dv02::oms {

inline constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

enum class Side { Buy, Sell, Hold };
std::string_view to_string(Side side);  // "BUY", "SELL", "HOLD"

// One row of the account's positions (build_paper_basket.fetch_positions_df).
struct Holding {
    std::string symbol;         // IBKR's symbol; a future or option's local symbol
    double quantity = 0.0;      // signed; NaN when the snapshot has none
    std::string sec_type;       // IBKR's secType; "" when not recorded
    double market_price = kNaN; // IBKR's mark; NaN when it has none
};

// One row of the latest targets.
struct Target {
    std::string symbol;
    double weight = 0.0;  // of NAV
    double close = kNaN;  // the targets' reference close
};

// The secTypes the basket trades: shares ("" is a snapshot written before
// sec_type was recorded, which held shares only).
bool is_shares(std::string_view sec_type);

// ── The drift gate ───────────────────────────────────────────────────────

struct Weights {
    std::map<std::string, double> weights;  // symbol (upper case) to fraction of NAV
    std::vector<std::string> unpriced;      // share holdings left out: no mark, no fallback price
};

// Each share holding's weight of NAV: shares times IBKR's mark, else the
// symbol's fallback price (the master's last close, then the targets' close).
// Futures, options and anything else that isn't shares are left out.
Weights current_weights(const std::vector<Holding>& holdings, double nav,
                        const std::map<std::string, double>& fallback_prices);

// Today's target weights as the gate compares them: upper-case symbols, and
// only the targets with a weight above 0.
std::map<std::string, double> gate_targets(const std::vector<Target>& targets);

struct DriftInput {
    std::map<std::string, double> target_weights;
    std::map<std::string, double> current_weights;
    nyse::Date today;
    std::optional<nyse::Date> last_submitted_at;
    std::optional<std::map<std::string, double>> last_basket;  // the last submission's targets
    double drift_threshold = 0.05;
    double composition_threshold = 0.05;
    int cooldown_days = 3;
    bool force = false;
    std::vector<std::string> outside_model;  // shares held outside the model, which the basket sells
};

struct DriftDecision {
    bool rebalance = false;
    std::string reason;
    double max_drift = 0.0;
    std::map<std::string, double> drift_per_symbol;
    bool composition_changed = false;
    bool in_cooldown = false;
    int cooldown_remaining_days = 0;
    std::vector<std::string> outside_model;
};

// Whether to rebalance today. Fires on, in this order: force; no last basket
// (a cold start); shares held outside the model; a change in the set of names
// with a target weight at or above composition_threshold; a name's |target -
// current| over drift_threshold. A submission fewer than cooldown_days calendar
// days ago holds back the last three, never the first two. Throws
// std::invalid_argument on a negative threshold or cooldown.
DriftDecision rebalance_decision(const DriftInput& in);

// ── The basket ───────────────────────────────────────────────────────────

struct BasketInput {
    std::vector<Target> targets;      // the latest targets
    std::vector<Holding> holdings;    // every position in the account
    double nav = 0.0;                 // NetLiquidation
    double cash_buffer = 0.005;       // fraction of NAV held back from the targets, and left in cash
    double min_trade_dollars = 1000.0;
    std::vector<std::string> model;      // when not empty, targets outside it are dropped
    std::vector<std::string> whitelist;  // when not empty, only these symbols trade
    std::map<std::string, double> last_closes;  // the master's last close, for holdings outside the model
    std::optional<double> cash;       // the account's cash now; without it nothing is trimmed
};

// One row of the reconciliation (build_reconciliation's first frame).
struct Line {
    std::string symbol;
    bool in_model = true;
    double close = kNaN;  // the targets' close; for a holding outside the model, its mark or last close
    double price = kNaN;  // what it is sized at: IBKR's mark when held, else close
    double target_weight = 0.0;
    double target_dollars = 0.0;
    std::int64_t target_shares = 0;
    std::int64_t current_shares = 0;
    std::int64_t delta_shares = 0;
    Side side = Side::Hold;
    std::int64_t abs_delta_shares = 0;
    std::int64_t trimmed_shares = 0;  // cut from a buy to stay inside the cash
    double estimated_trade_dollars = 0.0;
    double estimated_commission = 0.0;
    bool tradable = false;  // an order goes out for it
};

struct PlannedOrder {
    std::string symbol;
    Side side = Side::Hold;
    std::int64_t quantity = 0;
    double price = kNaN;
    double estimated_trade_dollars = 0.0;
    double estimated_commission = 0.0;
    bool in_model = true;
};

struct Basket {
    bool ok = true;
    std::string error;                // why there is no basket, when !ok (Python raises there)
    std::vector<Line> lines;          // sorted as the reconciliation file: side, then dollars, largest first
    std::vector<PlannedOrder> orders; // in sending order: every sell, then every buy
    std::vector<std::string> outside_sell;  // shares outside the model, sold whole
    std::vector<std::string> outside_kept;  // futures, options and the rest: left alone
    double investable = 0.0;          // NAV less the cash buffer
    double trim_keep = 1.0;           // the share of each buy kept to fit the cash; 1 when nothing was cut
    double trimmed_dollars = 0.0;
    std::vector<std::string> warnings;
};

// The reconciliation and the orders, from the latest targets and the account.
//
// Every target is sized to NAV less the cash buffer at `price`, whole shares
// rounded down, and goes out when its trade is at least min_trade_dollars.
// Shares held outside the model get a row with target 0 and are sold whole,
// whatever the size. With `cash`, buys are cut in proportion so the orders
// leave at least cash_buffer of NAV in cash; sells are never cut. A target
// that can't be priced is an error, as it is in Python.
Basket plan_basket(const BasketInput& in);

// ── The auction ──────────────────────────────────────────────────────────

enum class Auction { Close, Open };

struct Execution {
    std::string order_type;  // "MOC", or "MKT" with tif "OPG" for market-on-open
    std::string tif;         // "DAY" or "OPG"
    Auction auction = Auction::Close;
    nyse::Date session;      // the session whose close or open fills the orders
    nyse::Instant fills_at;
    std::optional<nyse::Instant> moc_deadline;  // for MOC: the last time the batch may be sent
};

// How long before the close an exchange stops taking market-on-close orders:
// NYSE and NYSE Arca 10 minutes, Nasdaq and Cboe BZX (BATS) 5. Anything else
// takes NYSE's.
nyse::Micros moc_cutoff(std::string_view exchange);
constexpr nyse::Micros kMocMargin = 5 * nyse::kMinute;
// The opening auctions take market-on-open orders until 09:28; from this time
// until the MOC deadline a batch goes market-on-close for the same day.
constexpr nyse::Micros kMooLastEntry = nyse::hm(9, 25);

// The last time a batch on these primary exchanges goes MOC for the session
// closing at `close`: the earliest cutoff among them, less the margin.
nyse::Instant moc_deadline(const nyse::Instant& close, const std::vector<std::string>& exchanges);

// Market-on-close at today's close when sent from 09:25 until the deadline on
// a trading day; otherwise market-on-open at the next open. `exchanges` are
// the batch's primary exchanges, one per symbol.
Execution execution_for(const std::vector<std::string>& exchanges, const nyse::Instant& at);

// ── Shared helpers ───────────────────────────────────────────────────────

// numpy's pairwise sum, which pandas' Series.sum() uses once NaN is set to 0.
double pairwise_sum(const std::vector<double>& values);
// Python's str.upper().strip() for ASCII symbols.
std::string clean_symbol(std::string_view symbol);

}  // namespace dv02::oms
