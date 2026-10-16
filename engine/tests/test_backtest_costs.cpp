// The cost model and the mandate check inside the backtester, against
// numbers worked out by hand in the comments.
#include <chrono>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "backtest_helpers.hpp"
#include "harness.hpp"

using namespace dv02::backtest;
using namespace dv02::backtest::test;
namespace costs = dv02::costs;

namespace {

Date ymd(int y, unsigned m, unsigned d) { return Date{std::chrono::year{y} / std::chrono::month{m} / std::chrono::day{d}}; }

// every cost off; tests switch on the one they check
costs::CostModel free_model() {
    costs::CostModel m;
    m.commissions = false;
    m.half_spread_bps = 0.0;
    m.impact_k = 0.0;
    m.borrow_bps = 0.0;
    m.cash_interest_bps = 0.0;
    for (auto& entry : m.futures) entry.second.roll_bps = 0.0;
    return m;
}

Settings with(costs::CostModel model, double initial_value = 100'000.0) {
    Settings s;
    s.initial_value = initial_value;
    s.costs = std::move(model);
    return s;
}

costs::Instrument future(double multiplier, std::string root = "") {
    return costs::Instrument{costs::Kind::Future, std::move(root), multiplier};
}

// a constant price for each asset on each date
Table flat(const std::vector<Date>& dates, const Row& prices) {
    std::vector<std::pair<Date, Row>> rows;
    for (const Date d : dates) rows.emplace_back(d, prices);
    return table(rows);
}

template <class F>
bool throws_invalid(F f, const std::string& fragment) {
    try {
        f();
    } catch (const std::invalid_argument& e) {
        return std::string(e.what()).find(fragment) != std::string::npos;
    }
    return false;
}

}  // namespace

// ── no cost model: the old numbers ────────────────────────────────────

TEST(btc_free_model_is_the_plain_backtest_bit_for_bit) {
    // 3 assets over 40 days, long/short events every 5 days, one over 100% gross
    std::vector<std::pair<Date, Row>> price_rows, weight_rows;
    const Date d0 = ymd(2024, 1, 2);
    for (int k = 0; k < 40; ++k) {
        const double t = static_cast<double>(k);
        price_rows.push_back({plus_days(d0, k),
                              {{"A", 100.0 + 5.0 * std::sin(t / 3.0)},
                               {"B", 50.0 + 0.25 * t},
                               {"C", 20.0 * std::exp(0.01 * std::cos(t))}}});
        if (k % 5 == 1)
            weight_rows.push_back({plus_days(d0, k), {{"A", 0.4 + 0.02 * t}, {"B", -0.3}, {"C", 0.5 - 0.01 * t}}});
    }
    const Table prices = table(price_rows), weights = table(weight_rows);
    const Result plain = run(prices, weights, Settings{.initial_value = 100'000.0});
    const Result costed = run(prices, weights, with(free_model()));

    CHECK(plain.portfolio_value.size() == 39);
    CHECK(plain.portfolio_value == costed.portfolio_value);  // exactly, not nearly
    CHECK(plain.cash == costed.cash);
    CHECK(plain.positions == costed.positions);
    CHECK(plain.daily_return == costed.daily_return);
    CHECK(costed.daily_total_cost == std::vector<double>(39, 0.0));
    CHECK(costed.metrics.cumulative_total_cost == 0.0);
    CHECK(plain.metrics.sharpe_ratio == costed.metrics.sharpe_ratio);
}

TEST(btc_without_a_model_the_new_columns_are_empty) {
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3)}, {{"SPY", 50.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"SPY", 0.5}}}});
    const Result r = run(prices, weights, Settings{.initial_value = 100'000.0});
    CHECK(r.daily_spread_cost.empty() && r.daily_impact_cost.empty() && r.daily_borrow_cost.empty());
    CHECK(r.daily_roll_cost.empty() && r.daily_interest_income.empty() && r.daily_total_cost.empty());
    CHECK(r.gross_exposure.empty() && r.mandate_cash.empty() && r.mandate_breach.empty());
    CHECK(r.metrics.cumulative_total_cost == 0.0 && r.metrics.mandate_breach_days == 0);
}

// ── commission ────────────────────────────────────────────────────────

TEST(btc_equity_commission_is_ibkr_pro_fixed) {
    // 50% of $100,000 in SPY at $50: 1,000 shares * $0.005 = $5.00
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3)}, {{"SPY", 50.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"SPY", 0.5}}}});
    costs::CostModel m = free_model();
    m.commissions = true;
    const Result r = run(prices, weights, with(m));

    CHECK_NEAR(r.daily_commission_cost[0], 5.0, 1e-12);
    CHECK_NEAR(r.metrics.cumulative_commission_cost, 5.0, 1e-12);
    CHECK_NEAR(r.daily_total_cost[0], 5.0, 1e-12);
    CHECK(r.daily_slippage_cost[0] == 0.0);
    CHECK_NEAR(r.portfolio_value[1], 99'995.0, 1e-9);
}

TEST(btc_futures_commission_per_contract) {
    // 50% of $100,000 in MES at 5,000 x $5 = $25,000 a contract: 2 contracts * $0.62
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3)}, {{"MES", 5'000.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"MES", 0.5}}}});
    costs::CostModel m = free_model();
    m.commissions = true;
    m.instruments = {{"MES", future(5.0)}};
    const Result r = run(prices, weights, with(m));

    CHECK_NEAR(r.daily_commission_cost[0], 1.24, 1e-12);
    CHECK_NEAR(r.portfolio_value[1], 100'000.0 - 1.24, 1e-9);
}

// ── spread ────────────────────────────────────────────────────────────

TEST(btc_half_spread_default_and_override) {
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3), ymd(2024, 1, 4)}, {{"SPY", 100.0}, {"QQQ", 200.0}});
    const Table weights =
        table({{ymd(2024, 1, 2), {{"SPY", 0.5}, {"QQQ", 0.3}}}, {ymd(2024, 1, 3), {{"SPY", 0.2}}}});
    costs::CostModel m = free_model();
    m.half_spread_bps = 2.0;
    m.half_spread_bps_by_symbol = {{"SPY", 0.5}};
    const Result r = run(prices, weights, with(m));

    // day 1: $50,000 SPY at 0.5 bp = 2.50, $30,000 QQQ at 2 bps = 6.00
    CHECK_NEAR(r.daily_spread_cost[0], 8.5, 1e-9);
    CHECK_NEAR(r.daily_slippage_cost[0], 8.5, 1e-9);  // spread + impact
    // day 2 on $99,991.50: SPY 50,000 -> 19,998.30 is 30,001.70 at 0.5 bp; QQQ left out, all
    // $30,000 sold at 2 bps
    CHECK_NEAR(r.daily_spread_cost[1], 30'001.7 * 0.5e-4 + 30'000.0 * 2e-4, 1e-9);
    CHECK(r.daily_spread_cost[2] == 0.0);
    CHECK_NEAR(r.metrics.cumulative_spread_cost, r.daily_spread_cost[0] + r.daily_spread_cost[1], 1e-12);
}

// ── impact ────────────────────────────────────────────────────────────

namespace {

// A: +2%, -2%, +2% before the trade date, which has a huge volume that must not count
Table impact_prices() {
    return table({{ymd(2024, 1, 2), {{"A", 100.0}}},
                  {ymd(2024, 1, 3), {{"A", 102.0}}},
                  {ymd(2024, 1, 4), {{"A", 99.96}}},
                  {ymd(2024, 1, 5), {{"A", 101.9592}}},
                  {ymd(2024, 1, 8), {{"A", 101.9592}}}});
}

Table impact_volumes() {
    return table({{ymd(2024, 1, 2), {{"A", 1'000.0}}},
                  {ymd(2024, 1, 3), {{"A", 1'000.0}}},
                  {ymd(2024, 1, 4), {{"A", 2'000.0}}},
                  {ymd(2024, 1, 5), {{"A", 3'000.0}}},
                  {ymd(2024, 1, 8), {{"A", 1e12}}}});
}

costs::CostModel impact_model() {
    costs::CostModel m = free_model();
    m.impact_k = 0.5;
    m.impact_lookback = 3;
    return m;
}

}  // namespace

TEST(btc_square_root_impact_from_history_before_the_trade) {
    const Table weights = table({{ymd(2024, 1, 8), {{"A", 0.5}}}});
    const Result r = run(impact_prices(), weights, impact_volumes(), with(impact_model()));

    // lookback 3: volatility from the closes of Jan 2-5 (returns +2%, -2%, +2%) = 0.04 / sqrt(3);
    // ADV from Jan 3-5: (102 x 1,000 + 99.96 x 2,000 + 101.9592 x 3,000) / 3 = $202,599.20;
    // $50,000 traded: 0.5 x sigma x sqrt(50,000 / 202,599.2) x $50,000
    const double sigma = 0.04 / std::sqrt(3.0), adv = 607'797.6 / 3.0;
    const double expected = 0.5 * sigma * std::sqrt(50'000.0 / adv) * 50'000.0;
    CHECK_NEAR(expected, 286.82, 0.01);
    CHECK_NEAR(r.daily_impact_cost[0], expected, 1e-9);
    CHECK_NEAR(r.daily_slippage_cost[0], expected, 1e-9);
    CHECK(r.metrics.unmeasured_impact_trades == 0);
    CHECK_NEAR(r.portfolio_value[0], 100'000.0 - expected, 1e-9);
}

TEST(btc_impact_falls_back_to_spread_only_without_data) {
    const Table weights = table({{ymd(2024, 1, 8), {{"A", 0.5}}}});
    costs::CostModel m = impact_model();
    m.half_spread_bps = 2.0;
    // no volumes: spread only, and the trade is counted as unmeasured
    const Result r = run(impact_prices(), weights, with(m));
    CHECK(r.daily_impact_cost[0] == 0.0);
    CHECK_NEAR(r.daily_spread_cost[0], 10.0, 1e-9);  // 2 bps of $50,000
    CHECK(r.metrics.unmeasured_impact_trades == 1);
    // a trade on the second price date has one close of history: no volatility
    const Table early = table({{ymd(2024, 1, 3), {{"A", 0.5}}}});
    const Result e = run(impact_prices(), early, impact_volumes(), with(m));
    CHECK(e.daily_impact_cost[0] == 0.0);
    CHECK(e.metrics.unmeasured_impact_trades == 1);
}

TEST(btc_impact_uses_the_futures_multiplier_for_dollar_volume) {
    // as above, but A is a future with multiplier 50: ADV is 50x, impact sqrt(1/50)x
    const Table weights = table({{ymd(2024, 1, 8), {{"A", 0.5}}}});
    costs::CostModel m = impact_model();
    m.futures["A"] = costs::FuturesRoot{0.0, 0.0, costs::kAllMonths, 31};
    m.instruments = {{"A", future(50.0)}};
    const Result r = run(impact_prices(), weights, impact_volumes(), with(m));
    const double sigma = 0.04 / std::sqrt(3.0), adv = 50.0 * 607'797.6 / 3.0;
    CHECK_NEAR(r.daily_impact_cost[0], 0.5 * sigma * std::sqrt(50'000.0 / adv) * 50'000.0, 1e-9);
}

// ── borrow ────────────────────────────────────────────────────────────

TEST(btc_borrow_on_shorts_over_calendar_days) {
    // a $50,000 SPY short from Friday: Monday pays 3 days, Tuesday 1, at 25 bps a year, actual/360
    const Table prices = flat({ymd(2024, 1, 5), ymd(2024, 1, 8), ymd(2024, 1, 9)}, {{"SPY", 100.0}});
    const Table weights = table({{ymd(2024, 1, 5), {{"SPY", -0.5}}}});
    costs::CostModel m = free_model();
    m.borrow_bps = 25.0;
    const Result r = run(prices, weights, with(m));
    CHECK(r.daily_borrow_cost[0] == 0.0);
    CHECK_NEAR(r.daily_borrow_cost[1], 50'000.0 * 0.0025 * 3.0 / 360.0, 1e-12);
    CHECK_NEAR(r.daily_borrow_cost[2], 50'000.0 * 0.0025 * 1.0 / 360.0, 1e-12);
    CHECK_NEAR(r.portfolio_value[2], 100'000.0 - r.metrics.cumulative_borrow_cost, 1e-9);

    // a hard-to-borrow override; a long pays nothing; a short future pays nothing
    m.borrow_bps_by_symbol = {{"SPY", 1'000.0}};
    CHECK_NEAR(run(prices, weights, with(m)).daily_borrow_cost[1], 50'000.0 * 0.10 * 3.0 / 360.0, 1e-12);
    const Table longs = table({{ymd(2024, 1, 5), {{"SPY", 0.5}}}});
    CHECK(run(prices, longs, with(m)).metrics.cumulative_borrow_cost == 0.0);
    m.instruments = {{"SPY", future(1.0, "ES")}};
    CHECK(run(prices, weights, with(m)).metrics.cumulative_borrow_cost == 0.0);
}

// ── cash interest ─────────────────────────────────────────────────────

TEST(btc_interest_on_positive_cash) {
    // 4% a year from Friday to Monday is 3 days, actual/360
    const Table prices = flat({ymd(2024, 1, 5), ymd(2024, 1, 8)}, {{"A", 100.0}, {"MES", 5'000.0}});
    costs::CostModel m = free_model();
    m.cash_interest_bps = 400.0;
    m.instruments = {{"MES", future(5.0)}};
    const auto monday = [&](const Row& w) {
        return run(prices, table({{ymd(2024, 1, 5), w}}), with(m)).daily_interest_income;
    };
    const double three_days = 0.04 * 3.0 / 360.0;
    CHECK(monday({{"A", 0.25}})[0] == 0.0);
    CHECK_NEAR(monday({{"A", 0.25}})[1], 75'000.0 * three_days, 1e-12);   // $75,000 of cash
    CHECK(monday({{"A", 1.5}})[1] == 0.0);                                  // a margin loan earns nothing
    CHECK_NEAR(monday({{"A", -0.5}})[1], 100'000.0 * three_days, 1e-12);  // short proceeds excluded
    CHECK_NEAR(monday({{"MES", 0.5}})[1], 100'000.0 * three_days, 1e-12); // futures move no cash

    const Result r = run(prices, table({{ymd(2024, 1, 5), {{"A", 0.25}}}}), with(m));
    CHECK_NEAR(r.portfolio_value[1], 100'000.0 + 25.0, 1e-9);
    CHECK_NEAR(r.daily_total_cost[1], -25.0, 1e-9);  // income is a negative cost
    CHECK_NEAR(r.metrics.cumulative_interest_income, 25.0, 1e-9);
}

TEST(btc_default_model_earns_interest_on_cash) {
    // The default model is what the live book pays and earns, interest included:
    // the cash of a 95% book earns IBKR's USD rate over the first weekend. (A
    // walk-forward smoke run once earned nothing on 5% cash: the default rate
    // was 0, which is "off".)
    const Table prices = flat({ymd(2024, 1, 5), ymd(2024, 1, 8), ymd(2024, 1, 9)}, {{"A", 100.0}});
    const costs::CostModel m;
    CHECK(m.cash_interest_bps > 0.0);
    const Result r = run(prices, table({{ymd(2024, 1, 5), {{"A", 0.95}}}}), with(m));
    // Friday closes with $5,000 less that day's $4.75 commission and $19 spread in cash
    // (no volumes: no impact); Monday accrues three calendar days on it, Tuesday one
    const double rate = m.cash_interest_bps / 10'000.0;
    CHECK_NEAR(r.cash[0], 5'000.0 - 4.75 - 19.0, 1e-9);
    CHECK(r.daily_interest_income[0] == 0.0);
    CHECK_NEAR(r.daily_interest_income[1], r.cash[0] * rate * 3.0 / 360.0, 1e-12);
    CHECK_NEAR(r.daily_interest_income[2], r.cash[1] * rate * 1.0 / 360.0, 1e-12);
    CHECK(r.daily_interest_income[1] > 1.0);  // about $1.40 at 3.38%
    CHECK_NEAR(r.metrics.cumulative_interest_income, r.daily_interest_income[1] + r.daily_interest_income[2], 1e-12);
    CHECK_NEAR(r.portfolio_value[2], 100'000.0 - 4.75 - 19.0 + r.metrics.cumulative_interest_income, 1e-9);
}

// ── futures rolls ─────────────────────────────────────────────────────

TEST(btc_futures_roll_once_a_roll_month) {
    // MES 5,000 x $5; $50,000 held from Thu Mar 7. It rolls on Mon Mar 11, the first price
    // date on or after the 10th: 1 bp of $50,000 = $5.00, plus 2 contracts x 2 legs x $0.62
    const std::vector<Date> days{ymd(2024, 3, 7), ymd(2024, 3, 8), ymd(2024, 3, 11), ymd(2024, 3, 12)};
    const Table prices = flat(days, {{"MES", 5'000.0}});
    costs::CostModel m = free_model();
    m.commissions = true;
    m.futures["MES"].roll_bps = 1.0;
    m.instruments = {{"MES", future(5.0)}};
    const Result r = run(prices, table({{days[0], {{"MES", 0.5}}}}), with(m));
    CHECK_NEAR(r.daily_commission_cost[0], 1.24, 1e-12);  // buying the 2 contracts
    CHECK(r.daily_roll_cost[0] == 0.0 && r.daily_roll_cost[1] == 0.0);
    CHECK_NEAR(r.daily_roll_cost[2], 5.0 + 2.48, 1e-12);
    CHECK(r.daily_roll_cost[3] == 0.0);
    CHECK_NEAR(r.metrics.cumulative_roll_cost, 7.48, 1e-12);

    // bought on the roll date itself: nothing to roll
    const Result fresh = run(prices, table({{days[2], {{"MES", 0.5}}}}), with(m));
    CHECK(fresh.metrics.cumulative_roll_cost == 0.0);
    // commissions off: the spread part only
    m.commissions = false;
    CHECK_NEAR(run(prices, table({{days[0], {{"MES", 0.5}}}}), with(m)).daily_roll_cost[2], 5.0, 1e-12);
}

TEST(btc_treasury_roll_after_a_weekend) {
    // ZN 110 x $1,000; $500,000 held from Thu Feb 22. The 24th is a Saturday, so it rolls on
    // Mon Feb 26: 1 bp = $50, plus 2 legs x 500,000 / 110,000 contracts x $1.67
    const std::vector<Date> days{ymd(2024, 2, 22), ymd(2024, 2, 23), ymd(2024, 2, 26)};
    costs::CostModel m = free_model();
    m.commissions = true;
    m.futures["ZN"].roll_bps = 1.0;
    m.instruments = {{"ZNH4", future(1'000.0, "ZN")}};
    const Result r = run(flat(days, {{"ZNH4", 110.0}}), table({{days[0], {{"ZNH4", 0.5}}}}), with(m, 1'000'000.0));
    CHECK_NEAR(r.daily_commission_cost[0], 500'000.0 / 110'000.0 * 1.67, 1e-9);
    CHECK(r.daily_roll_cost[1] == 0.0);
    CHECK_NEAR(r.daily_roll_cost[2], 50.0 + 2.0 * 500'000.0 / 110'000.0 * 1.67, 1e-9);
}

// ── all together ──────────────────────────────────────────────────────

TEST(btc_total_cost_is_what_the_book_loses) {
    // flat prices, so every dollar the book loses is a cost (or gains, interest)
    std::vector<Date> days;
    for (int k = 0; k < 30; ++k) days.push_back(plus_days(ymd(2024, 3, 1), k));
    const Table prices = flat(days, {{"SPY", 500.0}, {"TLT", 90.0}, {"MES", 5'000.0}});
    const Table volumes = flat(days, {{"SPY", 5e7}, {"TLT", 2e7}, {"MES", 1e5}});
    const Table weights = table({{days[5], {{"SPY", 0.4}, {"TLT", -0.2}, {"MES", 0.3}}},
                                 {days[12], {{"SPY", 0.2}, {"TLT", -0.3}, {"MES", 0.4}}},
                                 {days[25], {{"SPY", 0.5}}}});
    costs::CostModel m;  // the defaults, plus interest and the future
    m.cash_interest_bps = 400.0;
    m.instruments = {{"MES", future(5.0)}};
    const Result r = run(prices, weights, volumes, with(m));

    double total = 0.0;
    for (std::size_t k = 0; k < r.dates.size(); ++k) {
        const double parts = r.daily_commission_cost[k] + r.daily_spread_cost[k] + r.daily_impact_cost[k] +
                             r.daily_borrow_cost[k] + r.daily_roll_cost[k] - r.daily_interest_income[k];
        CHECK_NEAR(r.daily_total_cost[k], parts, 1e-9);
        CHECK_NEAR(r.daily_slippage_cost[k], r.daily_spread_cost[k] + r.daily_impact_cost[k], 1e-9);
        total += r.daily_total_cost[k];
    }
    CHECK_NEAR(r.metrics.cumulative_total_cost, total, 1e-9);
    CHECK_NEAR(r.portfolio_value.back(), 100'000.0 - total, 1e-6);
    CHECK(r.metrics.cumulative_commission_cost > 0.0 && r.metrics.cumulative_spread_cost > 0.0);
    CHECK(r.metrics.cumulative_borrow_cost > 0.0 && r.metrics.cumulative_interest_income > 0.0);
    CHECK(r.metrics.cumulative_roll_cost > 0.0);  // MES held through Mar 11
    CHECK(r.metrics.cumulative_impact_cost == 0.0);  // flat prices: zero volatility, zero impact
    CHECK(r.metrics.unmeasured_impact_trades == 0);  // five days of history before the first trade
}

TEST(btc_settings_errors) {
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3)}, {{"SPY", 50.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"SPY", 0.5}}}});
    Settings both = with(free_model());
    both.slippage_bps = 1.0;
    CHECK(throws_invalid([&] { run(prices, weights, both); }, "replaces slippage_bps and fee_model"));
    both.slippage_bps = 0.0;
    both.fee_model = FeeModel::IbkrProFixed;
    CHECK(throws_invalid([&] { run(prices, weights, both); }, "replaces slippage_bps and fee_model"));
    CHECK(throws_invalid([&] { run(prices, weights, prices, Settings{}); }, "volumes are read only by a cost model"));

    costs::CostModel unknown = free_model();
    unknown.instruments = {{"CLZ6", future(1'000.0, "CL")}};
    CHECK(throws_invalid([&] { run(prices, weights, with(unknown)); }, "no futures costs for root CL (symbol CLZ6)"));
    costs::CostModel bad = free_model();
    bad.impact_k = -1.0;
    CHECK(throws_invalid([&] { run(prices, weights, with(bad)); }, "impact_k"));
    // a volume table out of date order
    Table volumes = flat({ymd(2024, 1, 3), ymd(2024, 1, 2)}, {{"SPY", 1.0}});
    CHECK(throws_invalid([&] { run(prices, weights, volumes, with(free_model())); }, "volumes are not in date order"));
}

// ── the mandate check ─────────────────────────────────────────────────

namespace {

Settings mandate(Mandate m = {}) {
    Settings s;
    s.initial_value = 100'000.0;
    s.mandate = std::move(m);
    return s;
}

}  // namespace

TEST(btc_mandate_flags_gross_and_cash_and_never_rescales) {
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3)}, {{"A", 10.0}, {"B", 20.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"A", 0.6}, {"B", 0.6}}}});
    const Result r = run(prices, weights, mandate());
    // 120% gross on a $20,000 margin loan, both days
    CHECK_NEAR(r.gross_exposure[0], 1.2, 1e-12);
    CHECK_NEAR(r.mandate_cash[0], -0.2, 1e-12);
    CHECK(r.mandate_breach == std::vector<std::uint8_t>({1, 1}));
    CHECK(r.metrics.mandate_breach_days == 2);
    CHECK_NEAR(r.metrics.max_gross_exposure, 1.2, 1e-12);
    CHECK_NEAR(r.metrics.min_mandate_cash, -0.2, 1e-12);
    // flagged, not fixed: the book is what it would be without the check
    const Result plain = run(prices, weights, Settings{.initial_value = 100'000.0});
    CHECK(r.positions == plain.positions && r.cash == plain.cash && r.portfolio_value == plain.portfolio_value);
}

TEST(btc_mandate_fully_invested_is_inside) {
    const Table prices = flat({ymd(2024, 1, 2), ymd(2024, 1, 3)}, {{"A", 10.0}, {"B", 20.0}});
    const Result r = run(prices, table({{ymd(2024, 1, 2), {{"A", 0.7}, {"B", 0.3}}}}), mandate());
    CHECK_NEAR(r.gross_exposure[0], 1.0, 1e-12);
    CHECK(r.metrics.mandate_breach_days == 0);
}

TEST(btc_mandate_catches_drift_on_a_long_short_book) {
    // 50/50 long/short is 100% gross; the short rising 50% takes it to 125,000 / 75,000
    const Table prices = table({{ymd(2024, 1, 2), {{"A", 10.0}, {"B", 20.0}}}, {ymd(2024, 1, 3), {{"A", 10.0}, {"B", 30.0}}}});
    const Table weights = table({{ymd(2024, 1, 2), {{"A", 0.5}, {"B", -0.5}}}});
    const Result r = run(prices, weights, mandate());
    CHECK_NEAR(r.gross_exposure[0], 1.0, 1e-12);
    CHECK_NEAR(r.gross_exposure[1], 125'000.0 / 75'000.0, 1e-12);
    CHECK_NEAR(r.mandate_cash[1], 100'000.0 / 75'000.0, 1e-12);
    CHECK(r.mandate_breach == std::vector<std::uint8_t>({0, 1}));
}

TEST(btc_mandate_counts_exposure) {
    // $40,000 of TQQQ is $120,000 of exposure
    const Table prices = flat({ymd(2024, 1, 2)}, {{"TQQQ", 50.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"TQQQ", 0.4}}}});
    CHECK(run(prices, weights, mandate()).mandate_breach[0] == 0);
    const Result r = run(prices, weights, mandate(Mandate{.exposure = {{"TQQQ", 3.0}}}));
    CHECK_NEAR(r.gross_exposure[0], 1.2, 1e-12);
    CHECK(r.mandate_breach[0] == 1);
}

TEST(btc_mandate_futures_move_no_cash) {
    // with a cost model naming MES a future: 50% MES + 50% SPY leaves the book's cash at 0,
    // but a futures account would still hold the $50,000 the MES notional didn't spend
    const Table prices = flat({ymd(2024, 1, 2)}, {{"MES", 5'000.0}, {"SPY", 500.0}});
    costs::CostModel m = free_model();
    m.instruments = {{"MES", future(5.0)}};
    Settings s = with(m);
    s.mandate = Mandate{};
    const Result ok = run(prices, table({{ymd(2024, 1, 2), {{"MES", 0.5}, {"SPY", 0.5}}}}), s);
    CHECK_NEAR(ok.cash[0], 0.0, 1e-9);
    CHECK_NEAR(ok.mandate_cash[0], 0.5, 1e-12);
    CHECK_NEAR(ok.gross_exposure[0], 1.0, 1e-12);
    CHECK(ok.mandate_breach[0] == 0);
    // 90% MES + 50% SPY: cash is fine as futures count it, gross (futures at full notional) is not
    const Result over = run(prices, table({{ymd(2024, 1, 2), {{"MES", 0.9}, {"SPY", 0.5}}}}), s);
    CHECK_NEAR(over.mandate_cash[0], 0.5, 1e-12);
    CHECK_NEAR(over.gross_exposure[0], 1.4, 1e-12);
    CHECK(over.mandate_breach[0] == 1);
}

TEST(btc_mandate_costs_take_a_full_book_below_zero_cash) {
    // 100% SPY at $50 is 2,000 shares; their $10 commission is a $10 margin loan: leave a cash
    // buffer for costs
    const Table prices = flat({ymd(2024, 1, 2)}, {{"SPY", 50.0}});
    costs::CostModel m = free_model();
    m.commissions = true;
    Settings s = with(m);
    s.mandate = Mandate{};
    const Result r = run(prices, table({{ymd(2024, 1, 2), {{"SPY", 1.0}}}}), s);
    CHECK_NEAR(r.mandate_cash[0], -10.0 / 99'990.0, 1e-15);
    CHECK(r.mandate_breach[0] == 1);
    const Result buffered = run(prices, table({{ymd(2024, 1, 2), {{"SPY", 0.999}}}}), s);
    CHECK(buffered.mandate_breach[0] == 0);
}

TEST(btc_mandate_validation) {
    const Table prices = flat({ymd(2024, 1, 2)}, {{"SPY", 50.0}});
    const Table weights = table({{ymd(2024, 1, 2), {{"SPY", 0.5}}}});
    CHECK(throws_invalid([&] { run(prices, weights, mandate(Mandate{.max_gross = 0.0})); }, "max_gross"));
    CHECK(throws_invalid([&] { run(prices, weights, mandate(Mandate{.min_cash = kNaN})); }, "min_cash"));
    CHECK(throws_invalid([&] { run(prices, weights, mandate(Mandate{.exposure = {{"SPY", 0.0}}})); },
                         "exposure[SPY]"));
}
