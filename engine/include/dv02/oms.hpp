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
//   reconcile()          src/runtime/end_of_day_reconcile: the expected book against
//                        IBKR's positions, the run's first step
//   replay()             an order's status log as the order path reads it
//                        (src/broker/ibkr/orders.py, submit_paper_orders)
//   check_limits()       the pre-trade exposure limits on a basket's target
//                        weights (src/portfolio/risk/exposure_limits.py)
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
#include <utility>
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
    double multiplier = kNaN;   // contract size; NaN when IBKR gave none (1 for shares)
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

// ── The reconcile ────────────────────────────────────────────────────────
//
// The run's first step: what the account should hold after the orders sent
// since the last reconcile, against what IBKR holds, to the share. A break
// worth the threshold or more, or one with no price to value it, is material:
// Python then arms the kill switch.

// Quantities closer than this are equal (IBKR allows fractional shares).
inline constexpr double kShareTolerance = 1e-6;

// An order in the expected book (end_of_day_reconcile.record_submission).
struct BookOrder {
    std::string symbol;
    double quantity = 0.0;  // signed: sells negative
    std::string status;     // as recorded when it was sent
    std::string order_id;   // "" when none
    std::string perm_id;    // "" when none
    double price = kNaN;    // dollars per share it was sized at; NaN when none
};

struct ExpectedBook {
    std::map<std::string, double> positions;    // what the basket was built on
    std::map<std::string, double> unit_values;  // dollars per unit then
    std::vector<BookOrder> orders;              // sent since
};

// An execution IBKR reports (its API keeps the current day's).
struct Fill {
    std::string perm_id;
    std::string order_id;
    std::string symbol;
    double quantity = 0.0;  // signed: sells negative
};

struct CountedOrder {
    BookOrder order;
    double filled = 0.0;  // what it adds to the expected positions
    std::string counted;  // how: at its fills, in full, or not at all
};

struct PositionBreak {
    std::string symbol;
    double expected_qty = 0.0;
    double broker_qty = 0.0;
    double qty_diff = 0.0;  // broker less expected
    std::optional<double> ref_price;          // dollars per unit, when there is one
    std::optional<double> notional_diff_usd;  // none without a price (or at 0): material whatever its size
};

struct Held {
    std::map<std::string, double> quantities;   // symbol (upper case) to quantity
    std::map<std::string, double> unit_values;  // IBKR's mark times the multiplier
};

struct Reconcile {
    bool ok = true;
    std::string error;  // a position with no quantity: Python raises there
    std::map<std::string, double> expected;
    std::map<std::string, double> actual;
    std::map<std::string, double> unit_values;  // what the breaks were valued at
    std::vector<CountedOrder> orders;
    std::vector<PositionBreak> breaks;
    bool material = false;
};

// IBKR's positions as the reconcile reads them: zero positions dropped,
// duplicates summed, and each unit's dollar value where IBKR had a mark.
// A position with no finite quantity is an error.
Held held(const std::vector<Holding>& positions, std::string* error = nullptr);

// The reconcile: the book's positions plus each order at the fills IBKR
// reports for it (by permId, else orderId and symbol), else in full, or not at
// all when it never reached IBKR (status ERROR, Cancelled or ApiCancelled);
// then every symbol where IBKR's quantity differs, valued at IBKR's mark now,
// else the order's price, else the book's value.
Reconcile reconcile(const ExpectedBook& book, const std::vector<Holding>& positions, const std::vector<Fill>& fills,
                    double material_threshold_usd = 100.0);

// ── Order statuses ───────────────────────────────────────────────────────
//
// IBKR's order statuses as the Python order path reads them today:
//   unacknowledged  IBKR hasn't said anything yet: "", PendingSubmit, ApiPending
//                   (orders.UNACKNOWLEDGED)
//   working         it can still fill, so a second batch must not trade it again:
//                   PendingSubmit, ApiPending, PreSubmitted, Submitted, ApiUpdate,
//                   PendingCancel (submit_paper_orders.WORKING)
//   never placed    it never reached IBKR's book: the order path's own ERROR,
//                   Cancelled, ApiCancelled (end_of_day_reconcile.NOT_PLACED).
//                   Inactive, IBKR's rejection, isn't one: the reconcile counts a
//                   rejected order in full, and its missing shares show as a break.
bool unacknowledged(std::string_view status);
bool working(std::string_view status);
bool never_placed(std::string_view status);

// One entry of an order's status log (orders.trade_log): IBKR's status and any
// message it attached, in seconds after the order went out.
struct StatusEvent {
    double s = 0.0;
    std::string status;
    int code = 0;
    std::string message;
};

struct OrderTimeline {
    std::string status;                    // the last entry's: where the order stood when the log was kept
    std::optional<double> acknowledged_s;  // the first status past the unacknowledged ones (orders.acknowledged_after)
    std::vector<std::string> messages;     // each message IBKR attached, as "code: text"
    std::vector<std::string> anomalies;    // a status after a fill or a cancel, time running backwards
};

OrderTimeline replay(const std::vector<StatusEvent>& log);

// ── The pre-trade exposure limits ────────────────────────────────────────
//
// The limits on a basket's target weights (config/risk_limits.yaml), which the
// runner's gate checks before any order goes out, beside the no-leverage
// check on the whole book (risk::check). Breach names and wording are
// Python's, word for word.

struct ExposureLimits {
    double max_single_position = 0.25;
    double max_gross = 1.0;
    double min_net = 0.5;
    double max_net = 1.0;
    int min_holdings = 5;
    double max_single_trade_of_nav = 0.30;
    double min_cash_buffer = 0.0;
};

struct LimitBreach {
    std::string name;      // "max_single_position", "max_gross_exposure", "min_net_exposure", ...
    std::string detail;
    bool blocking = true;  // false for a warning: the single-trade size
};

struct LimitCheck {
    bool ok = true;
    std::string error;  // why it couldn't be judged, where Python raises (every trade unpriced)
    bool empty = false;  // no target weight at all
    std::vector<LimitBreach> breaches;
    double gross = 0.0;
    double net = 0.0;
    double long_exposure = 0.0;
    double short_exposure = 0.0;
    double max_single_position = 0.0;
    int holdings = 0;  // names with |weight| above 1e-6
    std::optional<double> max_single_trade;  // the largest trade, a fraction of NAV
    std::optional<double> cash_after;
};

using Weighted = std::vector<std::pair<std::string, double>>;

// `weights` and `trades` (dollars) are in the basket's order: sums run in it, and
// a tie for the largest goes to the first. NaN weights are dropped; without
// `trades` or `cash_after` those checks are skipped.
LimitCheck check_limits(const Weighted& weights, double nav, const std::optional<Weighted>& trades,
                        std::optional<double> cash_after, const ExposureLimits& limits = {});

// The cash a basket leaves as the gate measures it: 1 less its target weights
// (NaN counted as 0), and never below 0.
double cash_after(const Weighted& weights);

// ── Shared helpers ───────────────────────────────────────────────────────

// numpy's pairwise sum, which pandas' Series.sum() uses once NaN is set to 0.
double pairwise_sum(const std::vector<double>& values);
// Python's str.upper().strip() for ASCII symbols.
std::string clean_symbol(std::string_view symbol);

}  // namespace dv02::oms
