// The cost model's functions (include/dv02/costs.hpp), each against numbers
// worked out by hand in the comments.
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "dv02/backtest.hpp"
#include "dv02/costs.hpp"
#include "harness.hpp"

using namespace dv02::costs;

namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

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

// ── commissions ───────────────────────────────────────────────────────

TEST(costs_ibkr_pro_fixed_schedule) {
    CHECK_NEAR(ibkr_pro_fixed(50'000, 50), 5.0, 1e-12);     // 1,000 shares * $0.005
    CHECK_NEAR(ibkr_pro_fixed(1'000, 100), 1.0, 1e-12);     // 10 shares: the $1.00 minimum
    CHECK_NEAR(ibkr_pro_fixed(50'000, 0.10), 500.0, 1e-9);  // 500,000 shares, capped at 1% of $50,000
    CHECK_NEAR(ibkr_pro_fixed(50, 10), 0.5, 1e-12);         // the 1% cap wins over the minimum
    CHECK(ibkr_pro_fixed(0, 10) == 0.0);
    CHECK(ibkr_pro_fixed(100, 0) == 0.0);
}

TEST(costs_trade_functions_treat_nan_and_negatives_as_no_trade) {
    // an unmeasured or negative trade is free: never the $1.00 minimum, never a refund
    CHECK(ibkr_pro_fixed(kNaN, 50) == 0.0);
    CHECK(ibkr_pro_fixed(50'000, kNaN) == 0.0);
    CHECK(ibkr_pro_fixed(-50'000, 50) == 0.0);
    CHECK(spread_cost(-100'000, 2.0) == 0.0);
    CHECK(spread_cost(kNaN, 2.0) == 0.0);
    CHECK(impact_cost(-1e6, 0.02, 1e8, 0.5) == 0.0);
    CHECK(impact_cost(kNaN, 0.02, 1e8, 0.5) == 0.0);
}

TEST(costs_ibkr_pro_fixed_is_the_backtesters_fee_model) {
    // the backtester's fee_model charges exactly this, bit for bit
    for (const double value : {0.5, 13.0, 999.99, 1e4, 123'456.789, 5e7})
        for (const double price : {0.01, 0.1, 3.7, 50.0, 412.34, 9'999.0})
            CHECK(ibkr_pro_fixed(value, price) == dv02::backtest::ibkr_pro_fixed_commission(value, price));
}

TEST(costs_futures_commission_per_contract) {
    // MES at 5,000 x $5 = $25,000 a contract; $50,000 is 2 contracts at $0.62
    CHECK_NEAR(futures_commission(50'000, 5'000, 5, 0.62), 1.24, 1e-12);
    // ES at 5,000 x $50 = $250,000 a contract; $500,000 is 2 contracts at $2.25
    CHECK_NEAR(futures_commission(500'000, 5'000, 50, 2.25), 4.50, 1e-12);
    // fractional: $12,500 of MES is half a contract
    CHECK_NEAR(futures_commission(12'500, 5'000, 5, 0.62), 0.31, 1e-12);
    CHECK(futures_commission(0, 5'000, 5, 0.62) == 0.0);
    CHECK(futures_commission(50'000, kNaN, 5, 0.62) == 0.0);
    CHECK(futures_commission(50'000, 5'000, 0, 0.62) == 0.0);
}

TEST(costs_default_futures_table) {
    const auto table = default_futures();
    CHECK_NEAR(table.at("ES").commission, 2.25, 1e-12);   // 0.85 + 1.38 + 0.02
    CHECK_NEAR(table.at("NQ").commission, 2.25, 1e-12);
    CHECK_NEAR(table.at("MES").commission, 0.62, 1e-12);  // 0.25 + 0.35 + 0.02
    CHECK_NEAR(table.at("M2K").commission, 0.62, 1e-12);
    CHECK_NEAR(table.at("ZT").commission, 1.52, 1e-12);   // 0.85 + 0.65 + 0.02
    CHECK_NEAR(table.at("ZN").commission, 1.67, 1e-12);   // 0.85 + 0.80 + 0.02
    CHECK_NEAR(table.at("ZB").commission, 1.74, 1e-12);   // 0.85 + 0.87 + 0.02
    CHECK(table.at("MES").roll_months == kQuarterlyHMUZ && table.at("MES").roll_day == 10);
    CHECK(table.at("ZN").roll_months == kQuarterlyGKQX && table.at("ZN").roll_day == 24);
    for (const auto& [root, spec] : table) CHECK(spec.roll_bps == 1.0);
    CHECK(table.size() == 11);
}

// ── spread and impact ─────────────────────────────────────────────────

TEST(costs_spread) {
    CHECK_NEAR(spread_cost(100'000, 2.0), 20.0, 1e-12);  // 2 bps of $100,000
    CHECK_NEAR(spread_cost(50'000, 0.5), 2.5, 1e-12);
    CHECK(spread_cost(0, 2.0) == 0.0);
}

TEST(costs_square_root_impact) {
    // k 0.5, 2% daily vol, $1m traded against $100m ADV: sqrt(1%) = 0.1, so
    // 0.5 * 0.02 * 0.1 * 10,000 = 10 bps, $1,000
    CHECK_NEAR(impact_bps(1e6, 0.02, 1e8, 0.5), 10.0, 1e-9);
    CHECK_NEAR(impact_cost(1e6, 0.02, 1e8, 0.5), 1'000.0, 1e-6);
    // four times the size: twice the bps, eight times the dollars
    CHECK_NEAR(impact_bps(4e6, 0.02, 1e8, 0.5), 20.0, 1e-9);
    CHECK_NEAR(impact_cost(4e6, 0.02, 1e8, 0.5), 8'000.0, 1e-6);
    // k scales it linearly; k = 0 is free
    CHECK_NEAR(impact_bps(1e6, 0.02, 1e8, 1.0), 20.0, 1e-9);
    CHECK(impact_bps(1e6, 0.02, 1e8, 0.0) == 0.0);
    // no trade is free; unmeasured inputs are NaN, not free
    CHECK(impact_bps(0, 0.02, 1e8, 0.5) == 0.0);
    CHECK(std::isnan(impact_bps(1e6, kNaN, 1e8, 0.5)));
    CHECK(std::isnan(impact_bps(1e6, 0.02, kNaN, 0.5)));
    CHECK(std::isnan(impact_bps(1e6, 0.02, 0.0, 0.5)));
    CHECK(std::isnan(impact_cost(1e6, 0.02, 0.0, 0.5)));
}

TEST(costs_daily_volatility) {
    // returns +2%, -2%, +2%: mean 2%/3, sample variance 0.0016/3, so 0.04 / sqrt(3)
    const std::vector<double> closes{100.0, 102.0, 99.96, 101.9592};
    CHECK_NEAR(daily_volatility(closes), 0.04 / std::sqrt(3.0), 1e-12);
    // a gap breaks a pair: (100, 101) +1%, (102, 104.04) +2%, (104.04, 101.9592) -2%
    const std::vector<double> gappy{100.0, 101.0, kNaN, 102.0, 104.04, 101.9592};
    const double mean = 0.01 / 3.0;
    const double var = ((0.01 - mean) * (0.01 - mean) + (0.02 - mean) * (0.02 - mean) +
                        (-0.02 - mean) * (-0.02 - mean)) / 2.0;
    CHECK_NEAR(daily_volatility(gappy), std::sqrt(var), 1e-12);
    // fewer than 2 returns: unmeasured
    CHECK(std::isnan(daily_volatility(std::vector<double>{100.0, 102.0})));
    CHECK(std::isnan(daily_volatility(std::vector<double>{100.0, kNaN, 102.0, 0.0, 5.0})));
    CHECK(std::isnan(daily_volatility(std::vector<double>{})));
}

TEST(costs_average_dollar_volume) {
    // valid days: 10 x 100 and 40 x 25, $1,000 each
    const std::vector<double> closes{10.0, 20.0, kNaN, 40.0}, volumes{100.0, kNaN, 50.0, 25.0};
    CHECK_NEAR(average_dollar_volume(closes, volumes, 1.0), 1'000.0, 1e-9);
    CHECK_NEAR(average_dollar_volume(closes, volumes, 5.0), 5'000.0, 1e-9);  // contracts x multiplier
    // a zero-volume day counts; a negative one does not
    CHECK_NEAR(average_dollar_volume(std::vector<double>{10.0, 10.0}, std::vector<double>{0.0, 100.0}, 1.0), 500.0,
               1e-9);
    CHECK_NEAR(average_dollar_volume(std::vector<double>{10.0, 10.0}, std::vector<double>{-1.0, 100.0}, 1.0), 1'000.0,
               1e-9);
    CHECK(std::isnan(average_dollar_volume(std::vector<double>{kNaN}, std::vector<double>{100.0}, 1.0)));
}

// ── carry ─────────────────────────────────────────────────────────────

TEST(costs_borrow_accrues_actual_360) {
    // $100,000 short at 25 bps a year over a weekend: 100,000 * 0.0025 * 3 / 360
    CHECK_NEAR(borrow_cost(-100'000, 25.0, 3.0), 2.0833333333333335, 1e-12);
    CHECK_NEAR(borrow_cost(100'000, 25.0, 3.0), 2.0833333333333335, 1e-12);  // the sign doesn't matter
    // a hard-to-borrow name at 10% for one day
    CHECK_NEAR(borrow_cost(-36'000, 1'000.0, 1.0), 10.0, 1e-12);
    CHECK(borrow_cost(-100'000, 25.0, 0.0) == 0.0);
    // a day count never runs backwards, and an unmeasured short or count charges nothing
    CHECK(borrow_cost(-100'000, 25.0, -1.0) == 0.0);
    CHECK(borrow_cost(kNaN, 25.0, 1.0) == 0.0);
    CHECK(borrow_cost(-100'000, 25.0, kNaN) == 0.0);
}

TEST(costs_cash_interest_on_positive_cash_only) {
    // $360,000 at 4% for one day: 360,000 * 0.04 / 360 = $40
    CHECK_NEAR(cash_interest(360'000, 400.0, 1.0), 40.0, 1e-9);
    CHECK_NEAR(cash_interest(360'000, 400.0, 3.0), 120.0, 1e-9);
    CHECK(cash_interest(-360'000, 400.0, 1.0) == 0.0);  // negative cash pays nothing here
    CHECK(cash_interest(360'000, 0.0, 1.0) == 0.0);
    // nor does a day count that isn't positive, or cash that isn't a number
    CHECK(cash_interest(360'000, 400.0, 0.0) == 0.0);
    CHECK(cash_interest(360'000, 400.0, -1.0) == 0.0);
    CHECK(cash_interest(kNaN, 400.0, 1.0) == 0.0);
    CHECK(cash_interest(360'000, 400.0, kNaN) == 0.0);
}

// ── futures rolls ─────────────────────────────────────────────────────

TEST(costs_roll) {
    // one MES contract ($25,000 at 5,000 x 5): 1 bp of spread, $2.50, plus two legs at $0.62
    CHECK_NEAR(roll_cost(25'000, 5'000, 5, 1.0, 0.62), 2.5 + 1.24, 1e-12);
    CHECK_NEAR(roll_cost(-25'000, 5'000, 5, 1.0, 0.62), 2.5 + 1.24, 1e-12);  // a short rolls too
    CHECK_NEAR(roll_cost(25'000, 5'000, 5, 1.0, 0.0), 2.5, 1e-12);          // commissions off
    CHECK_NEAR(roll_cost(25'000, kNaN, 5, 1.0, 0.62), 2.5, 1e-12);           // no price: spread only
    CHECK(roll_cost(0, 5'000, 5, 1.0, 0.62) == 0.0);
}

TEST(costs_roll_window) {
    const auto table = default_futures();
    const FuturesRoot& es = table.at("ES");
    CHECK(!in_roll_window(es, 3, 9));
    CHECK(in_roll_window(es, 3, 10));
    CHECK(in_roll_window(es, 3, 31));
    CHECK(in_roll_window(es, 12, 15));
    CHECK(!in_roll_window(es, 4, 15));
    const FuturesRoot& zn = table.at("ZN");
    CHECK(in_roll_window(zn, 2, 24));
    CHECK(!in_roll_window(zn, 2, 23));
    CHECK(!in_roll_window(zn, 3, 24));
    CHECK(in_roll_window(zn, 11, 30));
    CHECK(!in_roll_window(zn, 0, 30));
    CHECK(!in_roll_window(zn, 13, 30));
}

// ── the model ─────────────────────────────────────────────────────────

TEST(costs_model_lookups) {
    CostModel m;
    m.half_spread_bps_by_symbol = {{"SPY", 0.5}};
    m.borrow_bps_by_symbol = {{"GME", 2'500.0}};
    m.instruments = {{"MES", Instrument{Kind::Future, "", 5.0}}, {"MESZ6", Instrument{Kind::Future, "MES", 5.0}}};
    CHECK(m.half_spread_for("SPY") == 0.5);
    CHECK(m.half_spread_for("QQQ") == 2.0);
    CHECK(m.borrow_for("GME") == 2'500.0);
    CHECK(m.borrow_for("SPY") == 25.0);
    CHECK(m.instrument_for("SPY").kind == Kind::Equity);
    CHECK(m.instrument_for("MES").kind == Kind::Future);
    CHECK_NEAR(m.futures_for("MES", m.instrument_for("MES")).commission, 0.62, 1e-12);  // root = the symbol
    CHECK_NEAR(m.futures_for("MESZ6", m.instrument_for("MESZ6")).commission, 0.62, 1e-12);
    validate(m);
    CHECK(to_string(Kind::Future) == "future" && to_string(Kind::Equity) == "equity");
}

TEST(costs_model_validation) {
    validate(CostModel{});
    const auto bad = [](auto edit, const std::string& fragment) {
        CostModel m;
        edit(m);
        return throws_invalid([&] { validate(m); }, fragment);
    };
    CHECK(bad([](CostModel& m) { m.half_spread_bps = -1.0; }, "half_spread_bps"));
    CHECK(bad([](CostModel& m) { m.half_spread_bps_by_symbol["SPY"] = kNaN; }, "half_spread_bps_by_symbol[SPY]"));
    CHECK(bad([](CostModel& m) { m.impact_k = -0.1; }, "impact_k"));
    CHECK(bad([](CostModel& m) { m.impact_lookback = 1; }, "impact_lookback"));
    CHECK(bad([](CostModel& m) { m.borrow_bps_by_symbol["GME"] = -5.0; }, "borrow_bps_by_symbol[GME]"));
    CHECK(bad([](CostModel& m) { m.cash_interest_bps = std::numeric_limits<double>::infinity(); }, "cash_interest"));
    CHECK(bad([](CostModel& m) { m.futures["ES"].roll_day = 0; }, "futures[ES].roll_day"));
    CHECK(bad([](CostModel& m) { m.futures["ES"].roll_months = 0; }, "futures[ES].roll_months"));
    CHECK(bad([](CostModel& m) { m.futures["ES"].roll_months = 0x1000; }, "futures[ES].roll_months"));
    CHECK(bad([](CostModel& m) { m.futures["ES"].commission = -1.0; }, "futures[ES].commission"));
    CHECK(bad([](CostModel& m) { m.instruments["CL"] = Instrument{Kind::Future, "", 1000.0}; }, "root CL"));
    CHECK(bad([](CostModel& m) { m.instruments["MES"] = Instrument{Kind::Future, "", 0.0}; }, "multiplier"));
    CHECK(bad([](CostModel& m) { m.instruments["SPY"] = Instrument{Kind::Equity, "", 5.0}; }, "is an equity"));
}
