#include "dv02/oms.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <set>
#include <stdexcept>

namespace dv02::oms {
namespace {

std::string upper(std::string_view s) {
    std::string out(s);
    for (auto& c : out) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return out;
}

// build_paper_basket._positive: finite and > 0, else NaN
double positive(double x) { return std::isfinite(x) && x > 0.0 ? x : kNaN; }
// build_paper_basket._quantity: finite, else 0
double finite_or_zero(double x) { return std::isfinite(x) ? x : 0.0; }

std::string fixed4(double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.4f", v);
    return buf;
}

std::string join(const std::vector<std::string>& items) {
    std::string out;
    for (std::size_t i = 0; i < items.size(); ++i) {
        if (i) out += ", ";
        out += items[i];
    }
    return out;
}

// pandas' Series.sum(): NaN counts as 0, then numpy's pairwise order.
double nansum(std::vector<double> values) {
    for (auto& v : values)
        if (std::isnan(v)) v = 0.0;
    return pairwise_sum(values);
}

double pairwise(const double* a, std::size_t n) {
    if (n < 8) {
        double res = 0.0;
        for (std::size_t i = 0; i < n; ++i) res += a[i];
        return res;
    }
    if (n <= 128) {
        double r[8];
        for (std::size_t k = 0; k < 8; ++k) r[k] = a[k];
        std::size_t i = 8;
        for (; i < n - (n % 8); i += 8)
            for (std::size_t k = 0; k < 8; ++k) r[k] += a[i + k];
        double res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; ++i) res += a[i];
        return res;
    }
    std::size_t n2 = n / 2;
    n2 -= n2 % 8;
    return pairwise(a, n2) + pairwise(a + n2, n - n2);
}

double lookup(const std::map<std::string, double>& m, const std::string& key) {
    const auto it = m.find(key);
    return it == m.end() ? 0.0 : it->second;
}

std::set<std::string> materially_weighted(const std::map<std::string, double>& weights, double min_weight) {
    std::set<std::string> out;
    for (const auto& [symbol, w] : weights)
        if (w >= min_weight) out.insert(symbol);
    return out;
}

// build_paper_basket._size_trades
void size_trades(std::vector<Line>& lines) {
    for (auto& l : lines) {
        l.side = l.delta_shares > 0 ? Side::Buy : (l.delta_shares < 0 ? Side::Sell : Side::Hold);
        l.abs_delta_shares = l.delta_shares < 0 ? -l.delta_shares : l.delta_shares;
        l.estimated_trade_dollars = static_cast<double>(l.abs_delta_shares) * l.price;
        l.estimated_commission =
            l.abs_delta_shares > 0 ? std::max(1.0, 0.01 * static_cast<double>(l.abs_delta_shares)) : 0.0;
    }
}

int side_rank(Side side) {  // the reconciliation sorts the side column as text: BUY, HOLD, SELL
    switch (side) {
        case Side::Buy: return 0;
        case Side::Hold: return 1;
        case Side::Sell: return 2;
    }
    return 1;
}

// Python's int(x) for a float shares count; false when x isn't a finite whole-share number.
bool to_shares(double x, std::int64_t& out) {
    if (!std::isfinite(x) || std::fabs(x) > 9.0e15) return false;
    out = static_cast<std::int64_t>(x);  // toward zero, as numpy's astype(int)
    return true;
}

}  // namespace

std::string_view to_string(Side side) {
    switch (side) {
        case Side::Buy: return "BUY";
        case Side::Sell: return "SELL";
        case Side::Hold: return "HOLD";
    }
    return "HOLD";
}

bool is_shares(std::string_view sec_type) { return sec_type.empty() || sec_type == "STK" || sec_type == "ETF"; }

std::string clean_symbol(std::string_view symbol) {
    std::size_t b = 0, e = symbol.size();
    while (b < e && std::isspace(static_cast<unsigned char>(symbol[b]))) ++b;
    while (e > b && std::isspace(static_cast<unsigned char>(symbol[e - 1]))) --e;
    return upper(symbol.substr(b, e - b));
}

double pairwise_sum(const std::vector<double>& values) { return 0.0 + pairwise(values.data(), values.size()); }

// ── The drift gate ───────────────────────────────────────────────────────

Weights current_weights(const std::vector<Holding>& holdings, double nav,
                        const std::map<std::string, double>& fallback_prices) {
    Weights out;
    for (const auto& h : holdings) {
        // NaN is not 0 here, as in Python, where `float(nan or 0.0)` stays NaN
        if (h.quantity == 0.0 || !is_shares(h.sec_type)) continue;
        const std::string symbol = clean_symbol(h.symbol);
        double price = h.market_price;
        if (!(price == price && price > 0.0)) {
            const auto it = fallback_prices.find(symbol);
            if (it == fallback_prices.end() || it->second == 0.0) {
                out.unpriced.push_back(symbol);
                continue;
            }
            price = it->second;
        }
        double& w = out.weights[symbol];
        w = w + h.quantity * price / nav;
    }
    return out;
}

std::map<std::string, double> gate_targets(const std::vector<Target>& targets) {
    std::map<std::string, double> out;
    for (const auto& t : targets)
        if (t.weight > 0.0) out[upper(t.symbol)] = t.weight;
    return out;
}

DriftDecision rebalance_decision(const DriftInput& in) {
    if (in.drift_threshold < 0.0) throw std::invalid_argument("drift_threshold must be >= 0");
    if (in.composition_threshold < 0.0) throw std::invalid_argument("composition_threshold must be >= 0");
    if (in.cooldown_days < 0) throw std::invalid_argument("cooldown_days must be >= 0");

    DriftDecision d;
    d.outside_model = in.outside_model;

    // drift on every name in either set; a missing side counts as 0%
    std::set<std::string> universe;
    for (const auto& [s, w] : in.target_weights) universe.insert(s);
    for (const auto& [s, w] : in.current_weights) universe.insert(s);
    bool first = true;
    for (const auto& s : universe) {
        const double drift = std::fabs(lookup(in.target_weights, s) - lookup(in.current_weights, s));
        d.drift_per_symbol[s] = drift;
        if (first || drift > d.max_drift) d.max_drift = drift;
        first = false;
    }

    if (in.last_basket)
        d.composition_changed = materially_weighted(*in.last_basket, in.composition_threshold) !=
                                materially_weighted(in.target_weights, in.composition_threshold);

    if (in.last_submitted_at && in.cooldown_days != 0) {
        const auto days_since = nyse::days_from_civil(in.today) - nyse::days_from_civil(*in.last_submitted_at);
        if (days_since >= 0) {  // a submission after today is clock skew: no cooldown
            d.in_cooldown = days_since < in.cooldown_days;
            d.cooldown_remaining_days = static_cast<int>(std::max<std::int64_t>(0, in.cooldown_days - days_since));
        }
    }

    const std::string wait = " (" + std::to_string(d.cooldown_remaining_days) + "d remaining)";
    auto decide = [&](bool rebalance, std::string reason) {
        d.rebalance = rebalance;
        d.reason = std::move(reason);
        return d;
    };

    if (in.force) return decide(true, "force flag set");
    if (!in.last_basket) return decide(true, "no prior basket recorded — cold start");
    if (!in.outside_model.empty()) {
        const std::string why = "holdings outside the model: " + join(in.outside_model);
        if (d.in_cooldown) return decide(false, why + ", but in cooldown" + wait);
        return decide(true, why);
    }
    if (d.composition_changed) {
        if (d.in_cooldown) return decide(false, "composition changed but in cooldown" + wait);
        return decide(true, "composition of materially-weighted basket changed since last submission");
    }
    if (d.max_drift > in.drift_threshold) {
        if (d.in_cooldown)
            return decide(false, "max drift " + fixed4(d.max_drift) + " > " + fixed4(in.drift_threshold) +
                                     " but in cooldown" + wait);
        return decide(true, "max drift " + fixed4(d.max_drift) + " exceeds band " + fixed4(in.drift_threshold));
    }
    return decide(false, "max drift " + fixed4(d.max_drift) + " within band " + fixed4(in.drift_threshold) + " — skip");
}

// ── The basket ───────────────────────────────────────────────────────────

Basket plan_basket(const BasketInput& in) {
    Basket b;

    // the targets' symbols are upper-cased, not stripped, as build_reconciliation does
    std::vector<Target> targets;
    targets.reserve(in.targets.size());
    for (Target t : in.targets) {
        t.symbol = upper(t.symbol);
        targets.push_back(std::move(t));
    }
    auto keep_only = [&targets](const std::vector<std::string>& names) {
        std::set<std::string> allowed;
        for (const auto& n : names) allowed.insert(clean_symbol(n));
        std::erase_if(targets, [&](const Target& t) { return !allowed.count(t.symbol); });
    };
    if (!in.model.empty()) keep_only(in.model);

    // holdings_outside_model, judged against the model's targets before the whitelist
    {
        std::set<std::string> targeted, whitelist;
        for (const auto& t : targets) targeted.insert(clean_symbol(t.symbol));
        for (const auto& w : in.whitelist) whitelist.insert(clean_symbol(w));
        for (const auto& h : in.holdings) {
            const std::string symbol = clean_symbol(h.symbol);
            const double qty = finite_or_zero(h.quantity);
            if (qty == 0.0 || targeted.count(symbol) || (!whitelist.empty() && !whitelist.count(symbol))) continue;
            (is_shares(h.sec_type) && std::fabs(qty) >= 1.0 ? b.outside_sell : b.outside_kept).push_back(symbol);
        }
        std::sort(b.outside_sell.begin(), b.outside_sell.end());
        std::sort(b.outside_kept.begin(), b.outside_kept.end());
    }
    for (const auto& s : b.outside_kept)
        b.warnings.push_back(s + ": outside the model, but not sold automatically (the basket trades whole shares "
                                 "of stocks and ETFs only)");

    if (!in.whitelist.empty()) keep_only(in.whitelist);
    if (targets.empty()) {
        b.ok = false;
        b.error = "No target rows remain after whitelist filtering";
        return b;
    }

    b.investable = in.nav * (1.0 - std::max(0.0, std::min(1.0, in.cash_buffer)));

    // one row per target: the shares held come from the position with exactly that symbol (a merge)
    std::vector<Line> lines;
    for (const auto& t : targets) {
        Line l;
        l.symbol = t.symbol;
        l.close = t.close;
        l.target_weight = t.weight;
        int matches = 0;
        double held = kNaN;
        for (const auto& h : in.holdings) {
            if (h.symbol != t.symbol) continue;
            if (++matches == 1) held = h.quantity;
        }
        if (matches > 1)
            b.warnings.push_back(t.symbol + " is in the positions " + std::to_string(matches) +
                                 " times; the first is used");
        if (!to_shares(std::isnan(held) ? 0.0 : held, l.current_shares)) {
            b.ok = false;
            b.error = "the position in " + t.symbol + " isn't a finite number of shares";
            return b;
        }
        lines.push_back(std::move(l));
    }
    // a row for each share holding outside the model: target 0, priced at IBKR's mark, else the last close
    for (const auto& symbol : b.outside_sell) {
        const Holding* h = nullptr;
        for (const auto& x : in.holdings)
            if (clean_symbol(x.symbol) == symbol) {
                h = &x;
                break;
            }
        Line l;
        l.symbol = symbol;
        l.in_model = false;
        const double mark = positive(h->market_price);
        const auto last = in.last_closes.find(symbol);
        l.close = std::isfinite(mark) ? mark : (last == in.last_closes.end() ? kNaN : last->second);
        to_shares(finite_or_zero(h->quantity), l.current_shares);
        lines.push_back(std::move(l));
    }

    // IBKR's mark for each symbol held (the last row wins, as a dict built row by row)
    std::map<std::string, double> marks;
    for (const auto& h : in.holdings) {
        const double mark = positive(h.market_price);
        if (std::isfinite(mark) && finite_or_zero(h.quantity) != 0.0) marks[clean_symbol(h.symbol)] = mark;
    }
    for (auto& l : lines) {
        const auto it = marks.find(l.symbol);
        l.price = it == marks.end() ? l.close : it->second;
        l.target_dollars = b.investable * l.target_weight;
        if (l.in_model) {
            if (!to_shares(std::floor(l.target_dollars / l.price), l.target_shares)) {
                b.ok = false;
                b.error = "the target in " + l.symbol + " can't be sized: no usable price";
                return b;
            }
        } else {
            l.target_shares = 0;
        }
        l.delta_shares = l.target_shares - l.current_shares;
        l.trimmed_shares = 0;
    }
    size_trades(lines);

    const double min_trade = in.min_trade_dollars;
    auto tradable = [min_trade](const Line& l) {
        return l.side != Side::Hold && l.abs_delta_shares > 0 &&
               (l.estimated_trade_dollars >= min_trade || !l.in_model);
    };

    // _fit_buys_to_cash: cut the buys in proportion so the orders leave the floor in cash
    if (in.cash && std::isfinite(*in.cash)) {
        const double cash_floor = in.cash_buffer * in.nav;
        std::vector<double> signed_dollars, commissions, bought;
        for (const auto& l : lines) {
            if (!tradable(l)) continue;
            signed_dollars.push_back(static_cast<double>(l.delta_shares) * l.price);
            commissions.push_back(l.estimated_commission);
            if (l.side == Side::Buy && l.in_model) bought.push_back(l.estimated_trade_dollars);
        }
        const double spend = nansum(signed_dollars) + nansum(commissions);
        const double shortfall = spend - (*in.cash - cash_floor);
        const double bought_dollars = nansum(bought);
        if (shortfall > 0.0 && bought_dollars > 0.0) {
            const double keep = std::max(0.0, (bought_dollars - shortfall) / bought_dollars);
            b.trim_keep = keep;
            for (auto& l : lines) {
                if (!(tradable(l) && l.side == Side::Buy && l.in_model)) continue;
                const auto kept = static_cast<std::int64_t>(std::floor(static_cast<double>(l.abs_delta_shares) * keep));
                l.trimmed_shares = l.abs_delta_shares - kept;
                l.delta_shares = kept;
                l.target_shares = l.current_shares + kept;
            }
            size_trades(lines);
        }
    }

    for (auto& l : lines) l.tradable = tradable(l);

    // the reconciliation's order: side as text, then dollars largest first with NaN last; ties keep their order
    std::stable_sort(lines.begin(), lines.end(), [](const Line& a, const Line& c) {
        const int ra = side_rank(a.side), rc = side_rank(c.side);
        if (ra != rc) return ra < rc;
        const double x = a.estimated_trade_dollars, y = c.estimated_trade_dollars;
        if (std::isnan(x)) return false;
        if (std::isnan(y)) return true;
        return x > y;
    });

    std::vector<double> cut;
    for (const auto& l : lines)
        if (l.trimmed_shares > 0) cut.push_back(static_cast<double>(l.trimmed_shares) * l.price);
    b.trimmed_dollars = nansum(cut);

    for (const auto& l : lines)
        if (l.tradable)
            b.orders.push_back({l.symbol, l.side, l.abs_delta_shares, l.price, l.estimated_trade_dollars,
                                l.estimated_commission, l.in_model});
    // submit_paper_orders.sells_first: every sell ahead of every buy, each side in file order
    std::stable_partition(b.orders.begin(), b.orders.end(), [](const PlannedOrder& o) { return o.side == Side::Sell; });

    b.lines = std::move(lines);
    return b;
}

// ── The reconcile ────────────────────────────────────────────────────────

Held held(const std::vector<Holding>& positions, std::string* error) {
    Held out;
    for (const auto& h : positions) {
        const std::string symbol = clean_symbol(h.symbol);
        if (!std::isfinite(h.quantity)) {
            if (error) *error = "position " + symbol + " has no quantity";
            return {};
        }
        if (std::fabs(h.quantity) <= kShareTolerance) continue;
        double& q = out.quantities[symbol];
        q = q + h.quantity;
        const double mark = positive(h.market_price);
        double multiplier = positive(h.multiplier);
        if (std::isnan(multiplier) && is_shares(h.sec_type)) multiplier = 1.0;
        if (std::isfinite(mark) && std::isfinite(multiplier)) out.unit_values[symbol] = mark * multiplier;
    }
    return out;
}

Reconcile reconcile(const ExpectedBook& book, const std::vector<Holding>& positions, const std::vector<Fill>& fills,
                    double material_threshold_usd) {
    Reconcile r;
    const Held account = held(positions, &r.error);
    if (!r.error.empty()) {
        r.ok = false;
        return r;
    }
    r.actual = account.quantities;

    std::map<std::string, double> expected = book.positions;
    for (const auto& order : book.orders) {
        CountedOrder c{order, 0.0, ""};
        std::vector<const Fill*> reported;
        for (const auto& f : fills) {
            if (!order.perm_id.empty() ? f.perm_id == order.perm_id
                                       : (!order.order_id.empty() && f.order_id == order.order_id &&
                                          f.symbol == order.symbol))
                reported.push_back(&f);
        }
        if (order.status == "ERROR" || order.status == "Cancelled" || order.status == "ApiCancelled") {
            c.counted = "not counted: it never reached IBKR (status " + order.status + ")";
        } else if (!reported.empty()) {
            double sum = 0.0;
            for (const Fill* f : reported) sum = sum + f->quantity;
            c.filled = sum;
            c.counted = "at the fills IBKR reports";
        } else {
            c.filled = order.quantity;
            c.counted = "in full: IBKR reports no fill for it (its API keeps the current day's)";
        }
        double& q = expected[order.symbol];
        q = q + c.filled;
        r.orders.push_back(std::move(c));
    }
    for (const auto& [symbol, q] : expected)
        if (std::fabs(q) > kShareTolerance) r.expected[symbol] = q;

    // valued at IBKR's mark now, else the price the order was sized at, else the book's value
    r.unit_values = book.unit_values;
    for (const auto& order : book.orders)
        if (std::isfinite(order.price) && order.price != 0.0) r.unit_values[order.symbol] = order.price;
    for (const auto& [symbol, v] : account.unit_values) r.unit_values[symbol] = v;

    std::set<std::string> symbols;
    for (const auto& [s, q] : r.expected) symbols.insert(s);
    for (const auto& [s, q] : r.actual) symbols.insert(s);
    for (const auto& symbol : symbols) {
        const double want = lookup(r.expected, symbol), have = lookup(r.actual, symbol);
        if (std::fabs(have - want) <= kShareTolerance) continue;
        PositionBreak b{symbol, want, have, have - want, std::nullopt, std::nullopt};
        const auto it = r.unit_values.find(symbol);
        if (it != r.unit_values.end()) {
            b.ref_price = it->second;
            if (it->second != 0.0) b.notional_diff_usd = std::fabs(have - want) * it->second;
        }
        // as Python: no notional is material; a NaN one compares false
        if (!b.notional_diff_usd || *b.notional_diff_usd >= material_threshold_usd) r.material = true;
        r.breaks.push_back(std::move(b));
    }
    return r;
}

// ── The auction ──────────────────────────────────────────────────────────

nyse::Micros moc_cutoff(std::string_view exchange) {
    if (exchange == "NASDAQ" || exchange == "BATS") return 5 * nyse::kMinute;
    return 10 * nyse::kMinute;  // NYSE, ARCA, and anything not known
}

nyse::Instant moc_deadline(const nyse::Instant& close, const std::vector<std::string>& exchanges) {
    nyse::Micros lead = exchanges.empty() ? moc_cutoff("NYSE") : 0;
    for (const auto& e : exchanges) lead = std::max(lead, moc_cutoff(e));
    return {close.date, close.time - lead - kMocMargin};
}

Execution execution_for(const std::vector<std::string>& exchanges, const nyse::Instant& at) {
    if (const auto session = nyse::session_for(at.date)) {
        const auto deadline = moc_deadline(session->close, exchanges);
        if (nyse::Instant{at.date, kMooLastEntry} <= at && at < deadline)
            return {"MOC", "DAY", Auction::Close, at.date, session->close, deadline};
    }
    const auto opens = nyse::next_open(at);
    return {"MKT", "OPG", Auction::Open, opens.date, opens, std::nullopt};
}

}  // namespace dv02::oms
