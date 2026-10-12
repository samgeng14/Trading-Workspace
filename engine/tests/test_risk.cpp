#include <string>

#include "dv02/risk.hpp"
#include "harness.hpp"

using namespace dv02::risk;

namespace {

Position etf(const std::string& s, double qty, double px) { return {s, qty, px, 1.0, AssetClass::ETF}; }
Order buy(const std::string& s, double qty, double px) { return {s, qty, px, 1.0, AssetClass::ETF, 0.0}; }

bool has(const Result& r, const std::string& rule, bool blocking) {
    for (const auto& b : r.breaches)
        if (b.rule == rule && b.blocking == blocking) return true;
    return false;
}

// the mandate alone: gross and cash, no single-name cap
Policy mandate() {
    Policy p;
    p.max_single_name = 1.0;
    return p;
}

const Breach* find(const Result& r, const std::string& rule) {
    for (const auto& b : r.breaches)
        if (b.rule == rule) return &b;
    return nullptr;
}

}  // namespace

TEST(unlevered_buy_passes) {
    Book book{1'000'000, 100'000, {etf("SPY", 450, 500), etf("TLT", 2250, 100), etf("IWM", 900, 250), etf("QQQ", 450, 500)}};
    auto r = check(book, {buy("GLD", 125, 400)});
    CHECK(r.ok);
    CHECK(r.breaches.empty());
    CHECK(!r.reduce_only);
    CHECK_NEAR(r.before.gross, 0.9, 1e-12);
    CHECK_NEAR(r.after.gross, 0.95, 1e-12);
    CHECK_NEAR(r.after.cash, 50'000, 1e-6);
    CHECK_NEAR(r.buy_capacity, 50'000, 1e-6);
}

TEST(buying_past_nav_blocks_on_gross_and_cash) {
    Book book{1'000'000, 10'000, {etf("SPY", 1980, 500)}};
    auto r = check(book, {buy("TLT", 400, 100)}, mandate());
    CHECK(!r.ok);
    CHECK(has(r, "gross", true));
    CHECK(has(r, "cash", true));
    CHECK_NEAR(r.after.gross, 1.03, 1e-12);
    CHECK_NEAR(r.after.cash, -30'000, 1e-6);
    const Breach* g = find(r, "gross");
    CHECK(g && g->message.find("$30,000") != std::string::npos);
    const Breach* c = find(r, "cash");
    CHECK(c && c->message.find("margin loan") != std::string::npos);
    CHECK_NEAR(r.buy_capacity, 0.0, 1e-9);
}

TEST(holdings_outside_the_model_count) {
    // the model is sized to 99.5% of NAV; three old holdings sit on top of it
    Book book{1'000'000, -32'000, {etf("SPY", 1990, 500), etf("DBC", 1000, 20), etf("XLE", 100, 90), etf("XLU", 100, 80)}};
    auto idle = check(book, {}, mandate());
    CHECK(idle.ok);  // nothing to send: the breach is reported, not blocking
    CHECK(has(idle, "gross", false));
    CHECK_NEAR(idle.before.gross, (995'000 + 20'000 + 9'000 + 8'000) / 1e6, 1e-12);

    // a rebalance that tops the model back up to 99.5% leaves the book levered: blocked
    auto r = check(book, {buy("SPY", -10, 500), buy("TLT", 50, 100)}, mandate());
    CHECK(!r.ok);
    CHECK(has(r, "gross", true));
    CHECK(has(r, "cash", true));
}

TEST(selling_down_a_levered_book_is_allowed) {
    Book book{1'000'000, -25'000, {etf("SPY", 2050, 500)}};  // 102.5% long, margin loan
    auto r = check(book, {buy("SPY", -20, 500)}, mandate());
    CHECK(r.reduce_only);
    CHECK(r.ok);
    CHECK(has(r, "gross", false));  // still over, reported, not blocking
    CHECK(has(r, "cash", false));
    CHECK_NEAR(r.after.gross, 1.015, 1e-12);
    const Breach* g = find(r, "gross");
    CHECK(g && g->message.find("only reduce") != std::string::npos);
}

TEST(a_basket_that_buys_anything_on_a_levered_book_blocks) {
    Book book{1'000'000, -25'000, {etf("SPY", 2050, 500)}};
    // sells more than it buys, so gross improves, but it still buys TLT
    auto r = check(book, {buy("SPY", -20, 500), buy("TLT", 50, 100)}, mandate());
    CHECK(!r.reduce_only);
    CHECK(!r.ok);
    CHECK(has(r, "gross", true));
}

TEST(futures_count_at_notional_and_move_no_cash) {
    Book book{1'000'000, 1'000'000, {}};
    Order mes{"MESZ6", -5, 6650, 5, AssetClass::Future, 2.5};
    auto r = check(book, {mes});
    CHECK(r.ok);
    CHECK_NEAR(r.after.short_value, 166'250, 1e-6);
    CHECK_NEAR(r.after.gross, 166'250 / (1'000'000 - 2.5), 1e-12);
    CHECK_NEAR(r.after.cash, 1'000'000 - 2.5, 1e-9);
    CHECK_NEAR(r.after.nav, 1'000'000 - 2.5, 1e-9);
}

TEST(futures_past_nav_block_even_with_cash) {
    Book book{1'000'000, 1'000'000, {}};
    Order es{"ESZ6", 4, 6650, 50, AssetClass::Future, 0};
    auto r = check(book, {es}, mandate());
    CHECK(!r.ok);
    CHECK(has(r, "gross", true));
    CHECK(find(r, "cash") == nullptr);
}

TEST(long_short_inside_gross_passes_and_one_more_share_blocks) {
    // 50% long, then 50% short: cash grows by the short proceeds
    Book book{1'000'000, 500'000, {etf("SPY", 1000, 500)}};
    auto r = check(book, {buy("IWM", -2000, 250)}, mandate());
    CHECK(r.ok);
    CHECK_NEAR(r.after.gross, 1.0, 1e-12);
    CHECK_NEAR(r.after.net, 0.0, 1e-12);
    CHECK_NEAR(r.after.cash, 1'000'000, 1e-6);

    auto r2 = check(book, {buy("IWM", -2001, 250)}, mandate());
    CHECK(!r2.ok);
    CHECK(has(r2, "gross", true));
}

TEST(unpriced_holdings_block) {
    Book book{1'000'000, 500'000, {etf("SPY", 100, 500), etf("DBC", 1000, 0)}};
    auto r = check(book, {});
    CHECK(!r.ok);
    const Breach* u = find(r, "unpriced");
    CHECK(u && u->blocking && u->message.find("DBC") != std::string::npos);
}

TEST(options_block_until_measured) {
    Book book{1'000'000, 1'000'000, {}};
    Order put{"SPY 260116P600", 1, 5.0, 100, AssetClass::Option, 1.0};
    auto r = check(book, {put});
    CHECK(!r.ok);
    CHECK(has(r, "options", true));
}

TEST(zero_nav_blocks) {
    auto r = check(Book{0, 0, {}}, {});
    CHECK(!r.ok);
    CHECK(has(r, "nav", true));
}

TEST(single_name_limit) {
    Book book{1'000'000, 700'000, {etf("SPY", 600, 500)}};
    auto r = check(book, {buy("SPY", 100, 500)});  // 35%
    CHECK(!r.ok);
    const Breach* s = find(r, "single_name");
    CHECK(s && s->blocking && s->message.find("SPY") != std::string::npos);
    Policy loose;
    loose.max_single_name = 0.5;
    CHECK(check(book, {buy("SPY", 100, 500)}, loose).ok);
}

TEST(net_limits) {
    Policy p;
    p.min_net = 0.0;  // no net short
    Book book{1'000'000, 1'000'000, {}};
    auto r = check(book, {buy("SPY", -200, 500)}, p);
    CHECK(!r.ok);
    CHECK(has(r, "net", true));
}

TEST(flipping_a_short_is_not_reduce_only) {
    Book book{1'000'000, 1'100'000, {etf("SPY", -200, 500)}};
    auto r = check(book, {buy("SPY", 300, 500)}, mandate());
    CHECK(!r.reduce_only);
    auto r2 = check(book, {buy("SPY", 200, 500)}, mandate());
    CHECK(r2.reduce_only);
    CHECK(r2.ok);
}

TEST(apply_re_marks_and_charges_commission) {
    Book book{1'000'000, 500'000, {etf("SPY", 1000, 500)}};
    Book after = apply(book, {Order{"SPY", 10, 510, 1.0, AssetClass::ETF, 1.0}});
    CHECK_NEAR(after.nav, 1'000'000 + 1000 * 10 - 1, 1e-9);
    CHECK_NEAR(after.cash, 500'000 - 5100 - 1, 1e-9);
    CHECK_NEAR(after.positions[0].quantity, 1010, 1e-12);
}

TEST(ibkr_sec_types) {
    CHECK(asset_class_from_ibkr("STK") == AssetClass::Stock);
    CHECK(asset_class_from_ibkr("FUT") == AssetClass::Future);
    CHECK(asset_class_from_ibkr("FOP") == AssetClass::FutureOption);
    CHECK(asset_class_from_ibkr("OPT") == AssetClass::Option);
    CHECK(asset_class_from_ibkr("CASH") == AssetClass::FX);
    CHECK(asset_class_from_ibkr("CRYPTO") == AssetClass::Crypto);
    CHECK(asset_class_from_ibkr("WHAT") == AssetClass::Other);
    CHECK(!settles_in_cash(AssetClass::Future));
    CHECK(settles_in_cash(AssetClass::ETF));
    CHECK(to_string(AssetClass::FutureOption) == "future_option");
}
