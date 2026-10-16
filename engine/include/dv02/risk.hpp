// Pre-trade risk: the fund's no-leverage mandate.
//
// The fund is long/short with no leverage:
//   gross = (long + |short|) / NAV <= 100%, with futures at notional and
//           leveraged or inverse ETFs at their economic exposure (TQQQ at 3x)
//   cash >= 0 after the orders, so no margin loan
//
// check() takes the whole account (every holding, not only the model's) plus
// the orders about to be sent, and says whether the book after those orders
// stays inside the policy. Anything it can't price blocks: leverage that
// can't be measured is treated as leverage.
#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace dv02::risk {

enum class AssetClass { Stock, ETF, Future, Option, FutureOption, Bond, FX, Crypto, Other };

// IBKR secType ("STK", "FUT", "OPT", "FOP", "BOND", "CASH", "CRYPTO", ...) to AssetClass.
AssetClass asset_class_from_ibkr(std::string_view sec_type);
std::string_view to_string(AssetClass asset_class);

// Futures and futures options move no cash when traded (only margin); every
// other asset class pays or receives quantity * price * multiplier.
bool settles_in_cash(AssetClass asset_class);

struct Position {
    std::string symbol;
    double quantity = 0.0;    // signed: negative is short
    double price = 0.0;       // per unit, in base currency
    double multiplier = 1.0;  // 1 for stocks and ETFs; contract size for futures and options
    AssetClass asset_class = AssetClass::Stock;
    // Economic exposure per dollar of market value: 3 for a 3x ETF, -1 for an
    // inverse ETF. Counts toward gross and net; cash still moves at market value.
    double exposure = 1.0;

    double market_value() const { return quantity * price * multiplier; }
    // Signed notional: what the position is exposed to.
    double notional() const { return market_value() * exposure; }
};

struct Order {
    std::string symbol;
    double quantity = 0.0;  // signed: buy > 0, sell < 0
    double price = 0.0;     // expected fill price
    double multiplier = 1.0;
    AssetClass asset_class = AssetClass::Stock;
    double commission = 0.0;
    double exposure = 1.0;  // as Position::exposure
};

struct Book {
    double nav = 0.0;
    double cash = 0.0;  // base currency; negative is a margin loan
    std::vector<Position> positions;
};

struct Policy {
    double max_gross = 1.0;        // (long + |short|) / NAV
    double min_cash = 0.0;         // cash / NAV; 0 forbids a margin loan
    double max_net = 1.0;          // (long - |short|) / NAV
    double min_net = -1.0;
    double max_single_name = 0.25; // |position| / NAV
};

struct Exposure {
    double nav = 0.0;
    double cash = 0.0;
    double long_value = 0.0;   // dollars
    double short_value = 0.0;  // dollars, positive
    double gross = 0.0;        // fraction of NAV
    double net = 0.0;          // fraction of NAV
    double cash_ratio = 0.0;   // cash / NAV
    std::string largest_name;
    double largest_weight = 0.0;  // |position| / NAV
    int positions = 0;            // non-zero positions
};

struct Breach {
    std::string rule;  // "nav", "unpriced", "gross", "cash", "net", "single_name", "options"
    bool blocking = true;
    double value = 0.0;
    double limit = 0.0;
    std::string message;
};

struct Result {
    bool ok = true;           // no blocking breach
    bool reduce_only = true;  // every order shrinks a position toward zero
    Exposure before;
    Exposure after;
    std::vector<Breach> breaches;
    // Dollars of further cash-settled buying the book could take after these
    // orders and still pass gross and cash. 0 when it is already at a limit.
    double buy_capacity = 0.0;
};

Exposure exposure(const Book& book);

// The book after the orders fill at their expected prices. Cash moves for
// cash-settled asset classes; commissions come out of cash and NAV.
Book apply(const Book& book, const std::vector<Order>& orders);

Result check(const Book& book, const std::vector<Order>& orders, const Policy& policy = {});

}  // namespace dv02::risk
