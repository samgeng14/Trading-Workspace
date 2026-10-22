// The NYSE calendar in New York local time: holidays, early closes, the
// regular session, the next open and the last completed session.
//
// A rule-for-rule port of trading-engine/src/common/clock.py, so the engine and
// the Python runner agree on every day (trading-engine/tests/unit/
// test_engine_oms.py compares them day by day). Times are wall-clock in New
// York; the caller converts to it (Python's zoneinfo). Nothing here knows about
// UTC offsets or daylight saving.
#pragma once

#include <compare>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace dv02::nyse {

struct Date {
    int year = 1970;
    int month = 1;  // 1..12
    int day = 1;    // 1..31
    friend auto operator<=>(const Date&, const Date&) = default;
};

// Days since 1970-01-01, proleptic Gregorian (Howard Hinnant's algorithms).
std::int64_t days_from_civil(const Date& d);
Date civil_from_days(std::int64_t days);
Date add_days(const Date& d, std::int64_t n);
bool valid(const Date& d);
// 0 = Monday ... 6 = Sunday, as Python's date.weekday().
int weekday(const Date& d);
std::string iso(const Date& d);  // "2026-10-21"

// A time of day, in microseconds after local midnight.
using Micros = std::int64_t;
constexpr Micros kMinute = 60'000'000;
constexpr Micros hm(int hour, int minute) { return (static_cast<Micros>(hour) * 60 + minute) * kMinute; }

constexpr Micros kOpen = hm(9, 30);
constexpr Micros kClose = hm(16, 0);
constexpr Micros kEarlyClose = hm(13, 0);
// IBKR's daily bar takes the closing auction's prints a few minutes after the
// close; until then the bar can still change (clock.DAILY_BAR_SETTLE).
constexpr Micros kBarSettle = 15 * kMinute;

// A local date and time.
struct Instant {
    Date date;
    Micros time = 0;
    friend auto operator<=>(const Instant&, const Instant&) = default;
};

struct Session {
    Instant open;
    Instant close;
};

// Easter Sunday (the anonymous Gregorian algorithm).
Date easter(int year);
// The year's NYSE holidays, as clock.py makes them: New Year's (observed, so a
// Saturday Jan 1 puts Dec 31 of the year before in this list, where no date of
// this year ever matches it), MLK, Presidents, Good Friday, Memorial,
// Juneteenth (from 2022), Independence Day (observed), Labor, Thanksgiving and
// Christmas (observed). Sorted.
std::vector<Date> holidays(int year);
bool is_business_day(const Date& d);
// 13:00 ET closes: July 3 when Independence Day falls Tuesday to Friday, the
// day after Thanksgiving, and Christmas Eve when it is a trading day.
bool is_early_close(const Date& d);
std::optional<Session> session_for(const Date& d);
// The first regular open strictly after `after`, looking up to 14 days ahead.
// Throws std::runtime_error when there is none (a calendar bug).
Instant next_open(const Instant& after);
// The last business day strictly before `d`.
Date previous_business_day(const Date& d);
// The latest session whose daily bar is final at `at`: today's from 15
// minutes after its close, else the business day before.
Date last_completed_session(const Instant& at);

}  // namespace dv02::nyse
