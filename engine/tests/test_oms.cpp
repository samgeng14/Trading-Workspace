#include <cmath>
#include <stdexcept>
#include <string>
#include <vector>

#include "dv02/oms.hpp"
#include "harness.hpp"

using namespace dv02::oms;
using dv02::nyse::Date;
using dv02::nyse::hm;
using dv02::nyse::Instant;

namespace {

Holding shares(const std::string& s, double qty, double mark = kNaN) { return {s, qty, "STK", mark}; }

BasketInput two_etfs() {
    BasketInput in;
    in.targets = {{"SPY", 0.6, 500.0}, {"TLT", 0.4, 100.0}};
    in.nav = 100'000.0;
    return in;
}

const Line* line(const Basket& b, const std::string& symbol) {
    for (const auto& l : b.lines)
        if (l.symbol == symbol) return &l;
    return nullptr;
}

DriftInput gate() {
    DriftInput in;
    in.target_weights = {{"SPY", 0.6}, {"TLT", 0.4}};
    in.current_weights = {{"SPY", 0.6}, {"TLT", 0.4}};
    in.today = {2026, 10, 21};
    in.last_basket = std::map<std::string, double>{{"SPY", 0.6}, {"TLT", 0.4}};
    return in;
}

}  // namespace

TEST(pairwise_sum_is_numpy_s) {
    CHECK(pairwise_sum({}) == 0.0);
    CHECK(pairwise_sum({1.0, 2.0, 3.0}) == 6.0);
    // 9 values: ((a0+a1)+(a2+a3))+((a4+a5)+(a6+a7)), then + a8, not left to right
    const std::vector<double> v{1e16, 1.0, -1e16, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0};
    const double pairwise = ((((1e16 + 1.0) + (-1e16 + 1.0)) + ((1.0 + 1.0) + (1.0 + 1.0))) + 1.0);
    CHECK(pairwise_sum(v) == pairwise);
}

TEST(clean_symbol_strips_and_upper_cases) {
    CHECK(clean_symbol("  spy\t") == "SPY");
    CHECK(clean_symbol("MESZ6") == "MESZ6");
    CHECK(clean_symbol("") == "");
}

TEST(basket_sizes_targets_to_nav_less_the_buffer) {
    BasketInput in = two_etfs();
    in.cash = 100'000.0;
    const Basket b = plan_basket(in);
    CHECK(b.ok);
    CHECK_NEAR(b.investable, 99'500.0, 1e-9);
    CHECK(b.lines.size() == 2);
    CHECK(b.lines[0].symbol == "SPY");  // buys, largest first
    CHECK(b.lines[0].target_shares == 119);
    CHECK_NEAR(b.lines[0].estimated_trade_dollars, 59'500.0, 1e-9);
    CHECK_NEAR(b.lines[0].estimated_commission, 1.19, 1e-12);
    CHECK(b.lines[1].target_shares == 398);
    CHECK(b.orders.size() == 2);
    CHECK(b.orders[0].symbol == "SPY" && b.orders[0].side == Side::Buy && b.orders[0].quantity == 119);
    CHECK(b.trim_keep == 1.0);
}

TEST(basket_cuts_buys_to_the_cash) {
    BasketInput in = two_etfs();
    in.cash = 50'000.0;
    const Basket b = plan_basket(in);
    CHECK(b.ok);
    // spend 99,305.17 against 49,500 of cash above the floor: keep (99,300 - 49,805.17) / 99,300
    CHECK_NEAR(b.trim_keep, (99'300.0 - 49'805.17) / 99'300.0, 1e-12);
    const Line* spy = line(b, "SPY");
    const Line* tlt = line(b, "TLT");
    CHECK(spy && spy->delta_shares == 59 && spy->trimmed_shares == 60 && spy->target_shares == 59);
    CHECK(tlt && tlt->delta_shares == 198 && tlt->trimmed_shares == 200);
    CHECK_NEAR(b.trimmed_dollars, 50'000.0, 1e-9);
    CHECK(b.orders.size() == 2);
}

TEST(basket_sells_holdings_outside_the_model_first) {
    BasketInput in = two_etfs();
    in.holdings = {shares("SPY", 100, 510.0), shares("XLE", 10, 90.0), {"MESZ6", 1, "FUT", 6000.0},
                   shares("ABC", 0.5, 20.0)};
    const Basket b = plan_basket(in);
    CHECK(b.ok);
    CHECK(b.outside_sell == std::vector<std::string>{"XLE"});
    CHECK((b.outside_kept == std::vector<std::string>{"ABC", "MESZ6"}));
    const Line* spy = line(b, "SPY");
    CHECK(spy && spy->price == 510.0 && spy->close == 500.0);  // sized at IBKR's mark
    CHECK(spy->target_shares == 117 && spy->delta_shares == 17);
    const Line* xle = line(b, "XLE");
    CHECK(xle && !xle->in_model && xle->delta_shares == -10 && xle->tradable);  // $900, under the minimum
    CHECK(b.orders.size() == 3);
    CHECK(b.orders[0].symbol == "XLE" && b.orders[0].side == Side::Sell && b.orders[0].quantity == 10);
    CHECK(b.orders[1].symbol == "TLT" && b.orders[2].symbol == "SPY");
    CHECK(b.lines.back().symbol == "XLE");  // the reconciliation lists sells after buys
}

TEST(basket_skips_trades_under_the_minimum) {
    BasketInput in = two_etfs();
    in.holdings = {shares("SPY", 118, 500.0), shares("TLT", 398, 100.0)};
    const Basket b = plan_basket(in);
    CHECK(b.ok);
    const Line* spy = line(b, "SPY");
    CHECK(spy && spy->side == Side::Buy && spy->abs_delta_shares == 1 && !spy->tradable);
    CHECK(b.orders.empty());
}

TEST(basket_drops_targets_outside_the_model) {
    BasketInput in = two_etfs();
    in.targets.push_back({"TQQQ", 0.1, 50.0});
    in.model = {"SPY", " tlt "};
    in.holdings = {shares("TQQQ", 30, 55.0)};
    const Basket b = plan_basket(in);
    CHECK(b.ok);
    CHECK(b.outside_sell == std::vector<std::string>{"TQQQ"});
    const Line* tqqq = line(b, "TQQQ");
    CHECK(tqqq && !tqqq->in_model && tqqq->target_weight == 0.0 && tqqq->delta_shares == -30);
}

TEST(basket_leaves_an_unpriced_outside_holding_unpriced) {
    BasketInput in = two_etfs();
    in.holdings = {shares("XLE", 10)};
    const Basket b = plan_basket(in);
    CHECK(b.ok);
    const Line* xle = line(b, "XLE");
    CHECK(xle && std::isnan(xle->price) && std::isnan(xle->estimated_trade_dollars) && xle->tradable);
    CHECK(b.orders.front().symbol == "XLE");  // the risk check blocks it as unpriced
    in.last_closes = {{"XLE", 88.0}};
    CHECK(line(plan_basket(in), "XLE")->price == 88.0);
}

TEST(basket_refuses_what_python_refuses) {
    BasketInput in = two_etfs();
    in.targets[0].close = kNaN;
    Basket b = plan_basket(in);
    CHECK(!b.ok && b.error.find("SPY") != std::string::npos);
    in = two_etfs();
    in.whitelist = {"GLD"};
    b = plan_basket(in);
    CHECK(!b.ok && b.error == "No target rows remain after whitelist filtering");
}

TEST(current_weights_price_at_the_mark_else_the_fallback) {
    const std::vector<Holding> held{shares("SPY", 100, 500.0), shares("tlt", 50), {"MESZ6", 1, "FUT", 6000.0},
                                    shares("XYZ", 5)};
    const Weights w = current_weights(held, 100'000.0, {{"TLT", 100.0}});
    CHECK(w.weights.size() == 2);
    CHECK_NEAR(w.weights.at("SPY"), 0.5, 1e-15);
    CHECK_NEAR(w.weights.at("TLT"), 0.05, 1e-15);
    CHECK(w.unpriced == std::vector<std::string>{"XYZ"});
    CHECK((gate_targets({{"spy", 0.6, 1.0}, {"GLD", 0.0, 1.0}}) == std::map<std::string, double>{{"SPY", 0.6}}));
}

TEST(gate_fires_in_order) {
    DriftInput in = gate();
    in.last_basket.reset();
    CHECK(rebalance_decision(in).reason == "no prior basket recorded — cold start");
    in = gate();
    in.force = true;
    CHECK(rebalance_decision(in).reason == "force flag set");

    in = gate();
    in.outside_model = {"XLE", "XLF"};
    DriftDecision d = rebalance_decision(in);
    CHECK(d.rebalance && d.reason == "holdings outside the model: XLE, XLF");
    in.last_submitted_at = Date{2026, 10, 20};
    d = rebalance_decision(in);
    CHECK(!d.rebalance && d.in_cooldown && d.cooldown_remaining_days == 2);
    CHECK(d.reason == "holdings outside the model: XLE, XLF, but in cooldown (2d remaining)");

    in = gate();
    in.target_weights = {{"SPY", 0.6}, {"GLD", 0.4}};
    in.current_weights = in.target_weights;
    d = rebalance_decision(in);
    CHECK(d.rebalance && d.composition_changed);
    CHECK(d.reason == "composition of materially-weighted basket changed since last submission");
}

TEST(gate_drift_band_and_cooldown) {
    DriftInput in = gate();
    in.current_weights = {{"SPY", 0.5}, {"TLT", 0.4}};
    DriftDecision d = rebalance_decision(in);
    CHECK(d.rebalance && d.reason == "max drift 0.1000 exceeds band 0.0500");
    in.last_submitted_at = Date{2026, 10, 19};
    d = rebalance_decision(in);
    CHECK(!d.rebalance && d.reason == "max drift 0.1000 > 0.0500 but in cooldown (1d remaining)");
    in.last_submitted_at = Date{2026, 10, 18};  // three days: out of cooldown
    CHECK(rebalance_decision(in).rebalance);
    in.last_submitted_at = Date{2026, 10, 25};  // after today: clock skew, no cooldown
    CHECK(rebalance_decision(in).rebalance && !rebalance_decision(in).in_cooldown);

    in = gate();
    in.current_weights = {{"SPY", 0.58}, {"TLT", 0.4}, {"XLE", 0.01}};
    d = rebalance_decision(in);
    CHECK(!d.rebalance && d.reason == "max drift 0.0200 within band 0.0500 — skip");
    CHECK_NEAR(d.drift_per_symbol.at("XLE"), 0.01, 1e-15);

    in.drift_threshold = -0.01;
    bool threw = false;
    try {
        rebalance_decision(in);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    CHECK(threw);
}

TEST(execution_moc_until_the_deadline) {
    const Date wed{2026, 10, 21};
    Execution e = execution_for({"ARCA"}, {wed, hm(15, 30)});
    CHECK(e.order_type == "MOC" && e.tif == "DAY" && e.auction == Auction::Close && e.session == wed);
    CHECK(e.fills_at == (Instant{wed, hm(16, 0)}));
    CHECK(e.moc_deadline && *e.moc_deadline == (Instant{wed, hm(15, 45)}));

    e = execution_for({"ARCA"}, {wed, hm(15, 45)});
    CHECK(e.order_type == "MKT" && e.tif == "OPG" && e.auction == Auction::Open);
    CHECK(e.session == (Date{2026, 10, 22}) && !e.moc_deadline);

    CHECK(execution_for({"NASDAQ"}, {wed, hm(15, 49)}).order_type == "MOC");    // 5-minute cutoff
    CHECK(execution_for({"ARCA", "NASDAQ"}, {wed, hm(15, 46)}).order_type == "MKT");  // the earliest cutoff
    CHECK(execution_for({}, {wed, hm(15, 46)}).order_type == "MKT");  // no symbols: NYSE's cutoff

    e = execution_for({"ARCA"}, {wed, hm(9, 24)});  // before 09:25: the open that is coming
    CHECK(e.auction == Auction::Open && e.fills_at == (Instant{wed, hm(9, 30)}));
    CHECK(execution_for({"ARCA"}, {wed, hm(9, 25)}).auction == Auction::Close);
}

TEST(execution_on_early_closes_and_weekends) {
    const Date black_friday{2026, 11, 27};
    Execution e = execution_for({"ARCA"}, {black_friday, hm(12, 44)});
    CHECK(e.order_type == "MOC" && *e.moc_deadline == (Instant{black_friday, hm(12, 45)}));
    e = execution_for({"ARCA"}, {black_friday, hm(12, 45)});
    CHECK(e.auction == Auction::Open && e.session == (Date{2026, 11, 30}));
    e = execution_for({"ARCA"}, {{2026, 10, 24}, hm(11, 0)});
    CHECK(e.auction == Auction::Open && e.session == (Date{2026, 10, 26}));
}

TEST(held_sums_duplicates_and_values_units_at_the_mark) {
    std::string error;
    const Held h = held({shares("spy", 100, 500.0), shares("SPY ", 5, 501.0), {"MESZ6", -2, "FUT", 6650.0, 5.0},
                         shares("TLT", 0.0, 100.0), {"ESZ6", 1, "FUT", 6650.0}},
                        &error);
    CHECK(error.empty());
    CHECK(h.quantities.size() == 3 && h.quantities.at("SPY") == 105.0 && h.quantities.at("MESZ6") == -2.0);
    CHECK(h.unit_values.at("SPY") == 501.0);           // the last row's mark
    CHECK(h.unit_values.at("MESZ6") == 6650.0 * 5.0);  // times the multiplier
    CHECK(!h.unit_values.count("ESZ6"));               // a future IBKR gave no multiplier for
    held({shares("SPY", kNaN)}, &error);
    CHECK(error == "position SPY has no quantity");
}

TEST(reconcile_counts_each_order_at_its_fills_in_full_or_not_at_all) {
    ExpectedBook book;
    book.positions = {{"SPY", 100.0}, {"TLT", 50.0}};
    book.unit_values = {{"SPY", 490.0}, {"TLT", 95.0}, {"GLD", 300.0}};
    book.orders = {{"SPY", 10.0, "PreSubmitted", "4", "355", 495.0},
                   {"TLT", -50.0, "PendingSubmit", "5", "356", kNaN},
                   {"GLD", 5.0, "ERROR", "", "", kNaN}};
    const std::vector<Fill> fills{{"355", "4", "SPY", 4.0}, {"355", "4", "SPY", 2.0}, {"999", "4", "SPY", 7.0}};
    Reconcile r = reconcile(book, {shares("SPY", 106, 500.0)}, fills);
    CHECK(r.ok && r.breaks.empty() && !r.material);
    CHECK(r.orders.size() == 3);
    CHECK(r.orders[0].filled == 6.0 && r.orders[0].counted == "at the fills IBKR reports");
    CHECK(r.orders[1].filled == -50.0 && r.orders[1].counted.rfind("in full", 0) == 0);
    CHECK(r.orders[2].filled == 0.0 && r.orders[2].counted == "not counted: it never reached IBKR (status ERROR)");
    CHECK(r.expected.size() == 1 && r.expected.at("SPY") == 106.0);  // TLT sold to zero

    // one share short in SPY ($500 at IBKR's mark: material), and XYZ nobody can price
    r = reconcile(book, {shares("SPY", 105, 500.0), shares("XYZ", 3)}, fills);
    CHECK(r.material && r.breaks.size() == 2);
    CHECK(r.breaks[0].symbol == "SPY" && r.breaks[0].qty_diff == -1.0 && *r.breaks[0].notional_diff_usd == 500.0);
    CHECK(r.breaks[1].symbol == "XYZ" && !r.breaks[1].ref_price && !r.breaks[1].notional_diff_usd);
}

TEST(reconcile_values_a_break_at_the_mark_then_the_order_then_the_book) {
    ExpectedBook book;
    book.positions = {{"GLD", 10.0}};
    book.unit_values = {{"GLD", 300.0}, {"VNQ", 80.0}};
    book.orders = {{"VNQ", 2.0, "Filled", "7", "", 90.0}};
    // VNQ: one share short, valued at the order's $90 (no mark now): under $100, not material
    Reconcile r = reconcile(book, {shares("GLD", 10, kNaN), shares("VNQ", 1)}, {});
    CHECK(!r.material && r.breaks.size() == 1 && *r.breaks[0].ref_price == 90.0);
    CHECK(reconcile(book, {shares("GLD", 10), shares("VNQ", 1)}, {}, 50.0).material);  // a lower threshold
    CHECK(!reconcile(book, {shares("SPY", kNaN)}, {}).ok);
}

TEST(order_status_sets_are_the_order_path_s) {
    CHECK(unacknowledged("") && unacknowledged("PendingSubmit") && unacknowledged("ApiPending"));
    CHECK(!unacknowledged("PreSubmitted") && !unacknowledged("Inactive"));
    CHECK(working("PreSubmitted") && working("PendingCancel") && !working("Filled") && !working("Inactive"));
    CHECK(never_placed("ERROR") && never_placed("ApiCancelled") && !never_placed("Inactive"));
}

TEST(replay_reads_an_orders_status_log) {
    OrderTimeline t = replay({{0.0, "PendingSubmit", 0, ""},
                              {12.4, "PendingSubmit", 399, "Order Message: Warning"},
                              {12.9, "PreSubmitted", 0, ""},
                              {1505.0, "Filled", 0, ""}});
    CHECK(t.status == "Filled" && t.acknowledged_s && *t.acknowledged_s == 12.9);
    CHECK(t.messages == std::vector<std::string>{"399: Order Message: Warning"} && t.anomalies.empty());
    t = replay({{0.0, "PendingSubmit", 0, ""}, {30.0, "PendingSubmit", 0, ""}});
    CHECK(t.status == "PendingSubmit" && !t.acknowledged_s);
    t = replay({{0.0, "PendingSubmit", 0, ""}, {2.0, "Filled", 0, ""}, {1.0, "Submitted", 0, ""}});
    CHECK(t.anomalies.size() == 2);
    CHECK(replay({}).status.empty());
}
