// Dates and tables for the backtester (the Rust input_handler_test.rs, case by
// case), plus the date rules spelled out.
#include <stdexcept>
#include <string>

#include "backtest_helpers.hpp"
#include "harness.hpp"

using namespace dv02::backtest;
using namespace dv02::backtest::test;

namespace {

// the message parse_date throws, or "" when it parses
std::string date_error(const std::string& text) {
    try {
        parse_date(text, 1);
    } catch (const std::invalid_argument& e) {
        return e.what();
    }
    return "";
}

}  // namespace

TEST(in_date_ordering) {
    const Table t = parse_table({"2023/01/15", "2023/01/01", "2023/01/30"}, {"A"}, {10.0, 9.0, 11.0});
    CHECK(to_string(t.dates[0]) == "2023-01-01");
    CHECK(to_string(t.dates[1]) == "2023-01-15");
    CHECK(to_string(t.dates[2]) == "2023-01-30");
    // prices move with their dates
    CHECK_NEAR(t.at(0, 0), 9.0, 1e-10);
    CHECK_NEAR(t.at(1, 0), 10.0, 1e-10);
    CHECK_NEAR(t.at(2, 0), 11.0, 1e-10);
}

TEST(in_date_format) {
    // single-digit, zero-padded and mixed: all the same date
    const Table t = parse_table({"2023/1/1", "2023/01/01", "2023/1/01"}, {"A"}, {10.0, 10.0, 10.0});
    for (const Date d : t.dates) CHECK(to_string(d) == "2023-01-01");
}

TEST(in_invalid_date_formats) {
    for (const char* text : {"2023.01.15", "01-15-2023", "2023-13-01", "2023-01-32", "not-a-date"}) {
        bool threw = false;
        try {
            parse_table({text}, {"A"}, {10.0});
        } catch (const std::invalid_argument&) {
            threw = true;
        }
        if (!threw) std::fprintf(stderr, "  expected an error for %s\n", text);
        CHECK(threw);
    }
}

TEST(in_weight_date_alignment) {
    const Table prices = parse_table({"2023/01/01", "2023/01/02", "2023/01/03"}, {"A"}, {10.0, 11.0, 12.0});
    const Table weights = parse_table({"2023/01/02"}, {"A"}, {1.0});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});
    // a row for every price date, not only the weight date
    CHECK(r.dates.size() == 3);
}

TEST(in_start_date_behavior) {
    const Date start = plus_days(today(), -5);
    const Table prices = table({{plus_days(start, -2), {{"A", 10.0}}},
                                {plus_days(start, -1), {{"A", 11.0}}},
                                {start, {{"A", 12.0}}},
                                {plus_days(start, 1), {{"A", 13.0}}}});
    const Table weights = table({{plus_days(start, -2), {{"A", 0.5}}}, {start, {{"A", 0.8}}}});
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});
    CHECK(r.dates.size() == 4);
}

TEST(in_date_gaps) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}},
                                {plus_days(now, 2), {{"A", 11.0}}},    // skips a day
                                {plus_days(now, 5), {{"A", 12.0}}}});  // skips two
    const Table weights = table({{now, {{"A", 0.5}}}, {plus_days(now, 3), {{"A", 0.8}}}});  // one in a gap
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});
    // only the dates with prices
    CHECK(r.dates.size() == 3);
    CHECK(r.dates == prices.dates);
}

TEST(in_future_weights) {
    const Date now = today();
    const Table prices = table({{now, {{"A", 10.0}}}, {plus_days(now, 1), {{"A", 11.0}}}, {plus_days(now, 2), {{"A", 12.0}}}});
    const Table weights = table({{now, {{"A", 0.5}}}, {plus_days(now, 5), {{"A", 0.8}}}});  // after the last price
    const Result r = run(prices, weights, {.initial_value = 1000.0, .start_date = prices.dates[0]});
    CHECK(r.dates.size() == 3);
}

TEST(in_mixed_date_formats) {
    const Table prices = parse_table({"2023-01-15", "2023/01/16", "2023-1-17", "2023/1/18", "2023-01-19"}, {"A"},
                                     {10.0, 11.0, 12.0, 13.0, 14.0});
    CHECK(to_string(prices.dates[0]) == "2023-01-15");
    CHECK(to_string(prices.dates[1]) == "2023-01-16");
    CHECK(to_string(prices.dates[2]) == "2023-01-17");
    CHECK(to_string(prices.dates[3]) == "2023-01-18");
    CHECK(to_string(prices.dates[4]) == "2023-01-19");

    const Table weights = parse_table({"2023-01-15", "2023/01/16"}, {"A"}, {0.5, 0.6});
    CHECK(to_string(weights.dates[0]) == "2023-01-15");
    CHECK(to_string(weights.dates[1]) == "2023-01-16");
}

TEST(in_weight_events_date_ordering) {
    const Table t = parse_table({"2023/01/15", "2023/01/30", "2023/01/01"}, {"A"}, {0.5, 0.6, 0.4});
    CHECK(to_string(t.dates[0]) == "2023-01-01");
    CHECK(to_string(t.dates[1]) == "2023-01-15");
    CHECK(to_string(t.dates[2]) == "2023-01-30");
    CHECK_NEAR(t.at(0, 0), 0.4, 1e-10);
    CHECK_NEAR(t.at(1, 0), 0.5, 1e-10);
    CHECK_NEAR(t.at(2, 0), 0.6, 1e-10);
}

TEST(in_weight_events_already_ordered) {
    const Table t = parse_table({"2023/01/01", "2023/01/15", "2023/01/30"}, {"A"}, {0.4, 0.5, 0.6});
    CHECK(to_string(t.dates[0]) == "2023-01-01");
    CHECK(to_string(t.dates[1]) == "2023-01-15");
    CHECK(to_string(t.dates[2]) == "2023-01-30");
    CHECK_NEAR(t.at(0, 0), 0.4, 1e-10);
    CHECK_NEAR(t.at(1, 0), 0.5, 1e-10);
    CHECK_NEAR(t.at(2, 0), 0.6, 1e-10);
}

// ── beyond the Rust tests ─────────────────────────────────────────────

TEST(in_date_rules) {
    CHECK(date_error("2024-02-29").empty());  // leap day
    CHECK(date_error("2023-02-29") == "Invalid date format at row 1: 2023-02-29");
    CHECK(date_error("+2023-01-05").empty());  // a '+' on the year, as the Rust parser allowed
    CHECK(date_error("2023-+1-+5").empty());   // and on month and day
    CHECK(date_error("2023-001-0005").empty());
    CHECK(date_error("23-01-05") == "Invalid date format at row 1: 23-01-05");    // the year is four digits
    CHECK(date_error("02023-01-05") == "Invalid date format at row 1: 02023-01-05");
    CHECK(date_error("2023-1/2-03") == "Invalid month at row 1: 1/2");            // a '-' anywhere splits on '-'
    CHECK(date_error("2023-01") == "Invalid date format at row 1: 2023-01");
    CHECK(date_error("2023-01-05-") == "Invalid date format at row 1: 2023-01-05-");
    CHECK(date_error("2023- 1-05") == "Invalid month at row 1:  1");
    CHECK(date_error("2023-01-256") == "Invalid day at row 1: 256");
    CHECK(date_error("2023-01-") == "Invalid day at row 1: ");
    CHECK(date_error("01-15-2023") == "Invalid day at row 1: 2023");
    CHECK(date_error("not-a-date") == "Invalid month at row 1: a");
    CHECK(date_error("2023.01.15") == "Invalid date format at row 1: 2023.01.15");
    CHECK(date_error("") == "Invalid date format at row 1: ");

    CHECK(to_string(parse_date("2023/1/5", 1)) == "2023-01-05");
    CHECK(to_string(parse_date("0001-01-01", 1)) == "0001-01-01");
    CHECK(parse_date("1970-01-02", 1).time_since_epoch().count() == 1);
}

TEST(in_errors_name_the_row) {
    try {
        parse_dates({"2023-01-01", "2023-01-02", "2023-02-30"});
        CHECK(false);
    } catch (const std::invalid_argument& e) {
        CHECK(std::string(e.what()) == "Invalid date format at row 3: 2023-02-30");
    }
}

TEST(in_sort_keeps_rows_with_one_date_in_order) {
    const Table t = parse_table({"2023-01-02", "2023-01-01", "2023-01-02", "2023-01-01"}, {"A", "B"},
                                {1, 10, 2, 20, 3, 30, 4, 40});
    CHECK((t.values == std::vector<double>{2, 20, 4, 40, 1, 10, 3, 30}));
}
