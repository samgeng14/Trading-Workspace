#include <algorithm>
#include <stdexcept>
#include <vector>

#include "dv02/nyse.hpp"
#include "harness.hpp"

using namespace dv02::nyse;

namespace {

bool listed(const std::vector<Date>& days, const Date& d) { return std::find(days.begin(), days.end(), d) != days.end(); }

}  // namespace

TEST(civil_days_round_trip) {
    CHECK(days_from_civil({1970, 1, 1}) == 0);
    CHECK(days_from_civil({2000, 3, 1}) == 11017);
    CHECK(civil_from_days(-1) == (Date{1969, 12, 31}));
    for (std::int64_t z = -800'000; z <= 800'000; z += 37) CHECK(days_from_civil(civil_from_days(z)) == z);
    CHECK(add_days({2024, 2, 28}, 1) == (Date{2024, 2, 29}));
    CHECK(add_days({2023, 2, 28}, 1) == (Date{2023, 3, 1}));
    CHECK(valid({2024, 2, 29}));
    CHECK(!valid({2023, 2, 29}));
    CHECK(!valid({2026, 13, 1}));
    CHECK(iso({2026, 10, 21}) == "2026-10-21");
}

TEST(weekday_is_python_s) {
    CHECK(weekday({1970, 1, 1}) == 3);    // Thursday
    CHECK(weekday({1969, 12, 31}) == 2);  // Wednesday
    CHECK(weekday({2026, 10, 21}) == 2);
    CHECK(weekday({2026, 10, 24}) == 5);  // Saturday
    CHECK(weekday({2026, 10, 25}) == 6);
    CHECK(weekday({2026, 10, 26}) == 0);
}

TEST(easter_dates) {
    CHECK(easter(2024) == (Date{2024, 3, 31}));
    CHECK(easter(2025) == (Date{2025, 4, 20}));
    CHECK(easter(2026) == (Date{2026, 4, 5}));
    CHECK(easter(2027) == (Date{2027, 3, 28}));
    CHECK(easter(2038) == (Date{2038, 4, 25}));
}

TEST(holidays_2026_and_2027) {
    const auto h26 = holidays(2026);
    CHECK(h26.size() == 10);
    for (const Date d : {Date{2026, 1, 1}, Date{2026, 1, 19}, Date{2026, 2, 16}, Date{2026, 4, 3}, Date{2026, 5, 25},
                         Date{2026, 6, 19}, Date{2026, 7, 3}, Date{2026, 9, 7}, Date{2026, 11, 26}, Date{2026, 12, 25}})
        CHECK(listed(h26, d));
    const auto h27 = holidays(2027);
    for (const Date d : {Date{2027, 1, 1}, Date{2027, 1, 18}, Date{2027, 2, 15}, Date{2027, 3, 26}, Date{2027, 5, 31},
                         Date{2027, 6, 18}, Date{2027, 7, 5}, Date{2027, 9, 6}, Date{2027, 11, 25}, Date{2027, 12, 24}})
        CHECK(listed(h27, d));
    CHECK(!listed(holidays(2021), Date{2021, 6, 18}));  // Juneteenth from 2022
}

TEST(saturday_new_year_leaves_dec_31_a_trading_day) {
    // Jan 1 2022 was a Saturday: clock.py lists Dec 31 2021 in 2022's holidays, where
    // no 2021 date is ever looked up, so Dec 31 trades (as NYSE did)
    CHECK(listed(holidays(2022), Date{2021, 12, 31}));
    CHECK(is_business_day({2021, 12, 31}));
}

TEST(business_days_and_early_closes) {
    CHECK(is_business_day({2026, 10, 21}));
    CHECK(!is_business_day({2026, 10, 24}));
    CHECK(!is_business_day({2026, 11, 26}));
    CHECK(is_early_close({2026, 11, 27}));  // the day after Thanksgiving
    CHECK(is_early_close({2026, 12, 24}));  // Christmas Eve, a Thursday
    CHECK(is_early_close({2025, 7, 3}));    // July 4 on a Friday
    CHECK(!is_early_close({2026, 7, 3}));   // the observed holiday itself
    CHECK(!is_early_close({2027, 12, 24})); // the observed Christmas
    CHECK(!is_early_close({2026, 10, 21}));
    const auto normal = session_for({2026, 10, 21});
    CHECK(normal && normal->open.time == kOpen && normal->close.time == kClose);
    const auto early = session_for({2026, 11, 27});
    CHECK(early && early->close.time == kEarlyClose);
    CHECK(!session_for({2026, 10, 25}));
}

TEST(next_open_is_strictly_after) {
    CHECK(next_open({{2026, 10, 21}, hm(8, 0)}) == (Instant{{2026, 10, 21}, kOpen}));
    CHECK(next_open({{2026, 10, 21}, kOpen}) == (Instant{{2026, 10, 22}, kOpen}));
    CHECK(next_open({{2026, 10, 23}, hm(16, 30)}) == (Instant{{2026, 10, 26}, kOpen}));
    CHECK(next_open({{2026, 11, 25}, hm(17, 0)}) == (Instant{{2026, 11, 27}, kOpen}));
}

TEST(last_completed_session_waits_for_the_bar) {
    CHECK(last_completed_session({{2026, 10, 21}, hm(16, 14)}) == (Date{2026, 10, 20}));
    CHECK(last_completed_session({{2026, 10, 21}, hm(16, 15)}) == (Date{2026, 10, 21}));
    CHECK(last_completed_session({{2026, 10, 24}, hm(12, 0)}) == (Date{2026, 10, 23}));
    CHECK(last_completed_session({{2026, 11, 27}, hm(13, 15)}) == (Date{2026, 11, 27}));
    CHECK(previous_business_day({2026, 10, 26}) == (Date{2026, 10, 23}));
}
