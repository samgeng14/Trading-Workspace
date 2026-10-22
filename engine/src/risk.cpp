#include "dv02/risk.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <string>

namespace dv02::risk {
namespace {

constexpr double kTol = 1e-9;

bool is_option(AssetClass ac) { return ac == AssetClass::Option || ac == AssetClass::FutureOption; }

bool priced(double quantity, double price, double multiplier, double exposure) {
    if (quantity == 0.0) return true;
    return std::isfinite(quantity) && std::isfinite(price) && price > 0.0 && std::isfinite(multiplier) &&
           multiplier > 0.0 && std::isfinite(exposure) && exposure != 0.0;
}

std::string money(double v) {
    const double r = std::round(std::fabs(v));
    char digits[32];
    std::snprintf(digits, sizeof digits, "%.0f", r);
    std::string d(digits);
    std::string out;
    const auto n = d.size();
    for (std::size_t i = 0; i < n; ++i) {
        if (i > 0 && (n - i) % 3 == 0) out.push_back(',');
        out.push_back(d[i]);
    }
    return (v < 0.0 && r > 0.0 ? "-$" : "$") + out;
}

std::string pct(double v) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%.1f%%", v * 100.0);
    return buf;
}

std::string join(const std::vector<std::string>& items, std::size_t max_items = 8) {
    std::string out;
    for (std::size_t i = 0; i < items.size() && i < max_items; ++i) {
        if (i) out += ", ";
        out += items[i];
    }
    if (items.size() > max_items) out += " and " + std::to_string(items.size() - max_items) + " more";
    return out;
}

// One limit, judged on the book after the orders. A breach blocks unless the
// orders only shrink positions and leave this measure no worse than before:
// selling down a levered book is always allowed; adding to one never is.
void judge(Result& r, const std::string& rule, bool breached_after, bool worse, double value, double limit,
           const std::string& what) {
    if (!breached_after) return;
    const bool blocking = !(r.reduce_only && !worse);
    std::string message = what;
    if (!blocking) message += "; these orders only reduce positions, so they may go";
    r.breaches.push_back({rule, blocking, value, limit, message});
    if (blocking) r.ok = false;
}

}  // namespace

AssetClass asset_class_from_ibkr(std::string_view sec_type) {
    if (sec_type == "STK") return AssetClass::Stock;
    if (sec_type == "ETF") return AssetClass::ETF;
    if (sec_type == "FUT" || sec_type == "CONTFUT") return AssetClass::Future;
    if (sec_type == "OPT" || sec_type == "WAR") return AssetClass::Option;
    if (sec_type == "FOP") return AssetClass::FutureOption;
    if (sec_type == "BOND" || sec_type == "BILL") return AssetClass::Bond;
    if (sec_type == "CASH") return AssetClass::FX;
    if (sec_type == "CRYPTO") return AssetClass::Crypto;
    return AssetClass::Other;
}

std::string_view to_string(AssetClass asset_class) {
    switch (asset_class) {
        case AssetClass::Stock: return "stock";
        case AssetClass::ETF: return "etf";
        case AssetClass::Future: return "future";
        case AssetClass::Option: return "option";
        case AssetClass::FutureOption: return "future_option";
        case AssetClass::Bond: return "bond";
        case AssetClass::FX: return "fx";
        case AssetClass::Crypto: return "crypto";
        case AssetClass::Other: return "other";
    }
    return "other";
}

bool settles_in_cash(AssetClass asset_class) {
    switch (asset_class) {
        case AssetClass::Future:
        case AssetClass::FutureOption:
        case AssetClass::FX:  // a conversion swaps one currency for another; base-currency cash is unchanged
            return false;
        default:
            return true;
    }
}

Exposure exposure(const Book& book) {
    Exposure e;
    e.nav = book.nav;
    e.cash = book.cash;
    std::map<std::string, double> by_name;
    for (const auto& p : book.positions) {
        if (p.quantity == 0.0 || !priced(p.quantity, p.price, p.multiplier, p.exposure)) continue;
        const double n = p.notional();
        if (n > 0.0) e.long_value += n;
        else e.short_value -= n;
        by_name[p.symbol] += n;
        ++e.positions;
    }
    if (book.nav > 0.0) {
        e.gross = (e.long_value + e.short_value) / book.nav;
        e.net = (e.long_value - e.short_value) / book.nav;
        e.cash_ratio = book.cash / book.nav;
        for (const auto& [name, n] : by_name) {
            const double w = std::fabs(n) / book.nav;
            if (w > e.largest_weight) {
                e.largest_weight = w;
                e.largest_name = name;
            }
        }
    }
    return e;
}

Book apply(const Book& book, const std::vector<Order>& orders) {
    Book out = book;
    for (const auto& o : orders) {
        if (o.quantity == 0.0) continue;
        auto it = std::find_if(out.positions.begin(), out.positions.end(),
                               [&](const Position& p) { return p.symbol == o.symbol; });
        if (it == out.positions.end()) {
            out.positions.push_back({o.symbol, o.quantity, o.price, o.multiplier, o.asset_class, o.exposure});
        } else {
            // re-mark what is already held at the expected fill price
            if (it->price > 0.0) out.nav += it->quantity * (o.price - it->price) * it->multiplier;
            it->quantity += o.quantity;
            it->price = o.price;
        }
        if (settles_in_cash(o.asset_class)) out.cash -= o.quantity * o.price * o.multiplier;
        out.cash -= o.commission;
        out.nav -= o.commission;
    }
    return out;
}

Result check(const Book& book, const std::vector<Order>& orders, const Policy& policy) {
    Result r;

    // ── inputs: anything unmeasurable blocks ─────────────────────────────
    if (!std::isfinite(book.nav) || book.nav <= 0.0) {
        r.ok = false;
        r.breaches.push_back({"nav", true, book.nav, 0.0, "NAV is " + money(book.nav) + ", so leverage can't be measured"});
    }
    if (!std::isfinite(book.cash)) {
        r.ok = false;
        r.breaches.push_back({"cash", true, 0.0, policy.min_cash, "cash balance is missing"});
    }
    std::vector<std::string> unpriced, options, geared;
    auto note_gearing = [&](const std::string& symbol, double exposure) {
        if (exposure == 1.0 || !std::isfinite(exposure)) return;
        char buf[32];
        std::snprintf(buf, sizeof buf, " %gx", exposure);
        const std::string item = symbol + buf;
        if (std::find(geared.begin(), geared.end(), item) == geared.end()) geared.push_back(item);
    };
    for (const auto& p : book.positions) {
        if (!priced(p.quantity, p.price, p.multiplier, p.exposure)) unpriced.push_back(p.symbol);
        if (p.quantity != 0.0 && is_option(p.asset_class)) options.push_back(p.symbol);
        if (p.quantity != 0.0) note_gearing(p.symbol, p.exposure);
    }
    std::map<std::string, double> traded;  // net order quantity per symbol
    for (const auto& o : orders) {
        if (!priced(o.quantity, o.price, o.multiplier, o.exposure) || !std::isfinite(o.commission))
            unpriced.push_back(o.symbol);
        if (o.quantity != 0.0) note_gearing(o.symbol, o.exposure);
        if (o.quantity != 0.0 && is_option(o.asset_class)) options.push_back(o.symbol);
        traded[o.symbol] += o.quantity;
    }
    if (!unpriced.empty()) {
        r.ok = false;
        r.breaches.push_back({"unpriced", true, static_cast<double>(unpriced.size()), 0.0,
                              "no usable price for " + join(unpriced) + ", so leverage can't be measured"});
    }
    if (!options.empty()) {
        // premium understates what an option is exposed to; until delta-adjusted
        // notional is wired in, options can't pass a leverage check
        r.ok = false;
        r.breaches.push_back({"options", true, static_cast<double>(options.size()), 0.0,
                              "options (" + join(options) +
                                  ") need delta-adjusted notional, which this check doesn't measure yet"});
    }

    // ── reduce-only: every traded name moves toward zero without flipping ─
    for (const auto& [symbol, dq] : traded) {
        if (dq == 0.0) continue;
        double q0 = 0.0;
        for (const auto& p : book.positions)
            if (p.symbol == symbol) q0 += p.quantity;
        const double q1 = q0 + dq;
        if (std::fabs(q1) > std::fabs(q0) + kTol || q0 * q1 < 0.0) {
            r.reduce_only = false;
            break;
        }
    }

    const Book after = apply(book, orders);
    r.before = exposure(book);
    r.after = exposure(after);
    const Exposure& b = r.before;
    const Exposure& a = r.after;
    if (!(a.nav > 0.0)) return r;

    // ── the mandate ───────────────────────────────────────────────────────
    const double gross_dollars = a.long_value + a.short_value;
    judge(r, "gross", a.gross > policy.max_gross + kTol, a.gross > b.gross + kTol, a.gross, policy.max_gross,
          "gross " + pct(a.gross) + " of NAV after these orders, over the " + pct(policy.max_gross) + " limit by " +
              money(gross_dollars - policy.max_gross * a.nav) +
              (geared.empty() ? std::string() : " (counting " + join(geared) + " at that exposure)"));
    judge(r, "cash", a.cash_ratio < policy.min_cash - kTol, a.cash_ratio < b.cash_ratio - kTol, a.cash_ratio,
          policy.min_cash,
          "cash " + money(a.cash) + " after these orders" +
              (policy.min_cash == 0.0 ? std::string(": a margin loan") : ", under the " + pct(policy.min_cash) + " floor") +
              ", short by " + money(policy.min_cash * a.nav - a.cash));
    // |net| <= gross, so a net limit at or beyond the gross limit can only repeat it
    if (policy.max_net < policy.max_gross)
        judge(r, "net", a.net > policy.max_net + kTol, a.net > b.net + kTol, a.net, policy.max_net,
              "net " + pct(a.net) + " of NAV after these orders, over the " + pct(policy.max_net) + " limit");
    if (policy.min_net > -policy.max_gross)
        judge(r, "net", a.net < policy.min_net - kTol, a.net < b.net - kTol, a.net, policy.min_net,
              "net " + pct(a.net) + " of NAV after these orders, under the " + pct(policy.min_net) + " limit");
    judge(r, "single_name", a.largest_weight > policy.max_single_name + kTol, a.largest_weight > b.largest_weight + kTol,
          a.largest_weight, policy.max_single_name,
          a.largest_name + " is " + pct(a.largest_weight) + " of NAV after these orders, over the " +
              pct(policy.max_single_name) + " limit");

    r.buy_capacity = std::max(0.0, std::min(policy.max_gross * a.nav - gross_dollars, a.cash - policy.min_cash * a.nav));
    return r;
}

}  // namespace dv02::risk
