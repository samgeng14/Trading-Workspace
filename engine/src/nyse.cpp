#include "dv02/nyse.hpp"

#include <algorithm>
#include <cstdio>
#include <stdexcept>

namespace dv02::nyse {
namespace {

// Python's a % b for b > 0: never negative.
int mod(int a, int b) { return ((a % b) + b) % b; }

Date nth_weekday(int year, int month, int wd, int n) {
    const Date first{year, month, 1};
    const int offset = mod(wd - weekday(first), 7);
    return add_days(first, offset + 7 * (n - 1));
}

Date last_weekday(int year, int month, int wd) {
    const Date next_month = month == 12 ? Date{year + 1, 1, 1} : Date{year, month + 1, 1};
    const Date last_day = add_days(next_month, -1);
    return add_days(last_day, -mod(weekday(last_day) - wd, 7));
}

// A Saturday holiday is observed on the Friday before, a Sunday one on the Monday after.
Date observed(const Date& d) {
    const int wd = weekday(d);
    if (wd == 5) return add_days(d, -1);
    if (wd == 6) return add_days(d, 1);
    return d;
}

bool contains(const std::vector<Date>& days, const Date& d) {
    return std::binary_search(days.begin(), days.end(), d);
}

}  // namespace

std::int64_t days_from_civil(const Date& d) {
    const std::int64_t y = d.year - (d.month <= 2 ? 1 : 0);
    const std::int64_t era = (y >= 0 ? y : y - 399) / 400;
    const std::int64_t yoe = y - era * 400;
    const std::int64_t mp = (d.month + 9) % 12;
    const std::int64_t doy = (153 * mp + 2) / 5 + d.day - 1;
    const std::int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

Date civil_from_days(std::int64_t days) {
    const std::int64_t z = days + 719468;
    const std::int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const std::int64_t doe = z - era * 146097;
    const std::int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const std::int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const std::int64_t mp = (5 * doy + 2) / 153;
    const std::int64_t day = doy - (153 * mp + 2) / 5 + 1;
    const std::int64_t month = mp < 10 ? mp + 3 : mp - 9;
    const std::int64_t year = yoe + era * 400 + (month <= 2 ? 1 : 0);
    return {static_cast<int>(year), static_cast<int>(month), static_cast<int>(day)};
}

Date add_days(const Date& d, std::int64_t n) { return civil_from_days(days_from_civil(d) + n); }

bool valid(const Date& d) {
    if (d.month < 1 || d.month > 12 || d.day < 1) return false;
    return civil_from_days(days_from_civil(d)) == d;
}

int weekday(const Date& d) {
    // 1970-01-01 was a Thursday (3)
    const std::int64_t z = days_from_civil(d);
    return static_cast<int>(((z % 7) + 7 + 3) % 7);
}

std::string iso(const Date& d) {
    char buf[16];
    std::snprintf(buf, sizeof buf, "%04d-%02d-%02d", d.year, d.month, d.day);
    return buf;
}

Date easter(int year) {
    const int a = year % 19;
    const int b = year / 100;
    const int c = year % 100;
    const int d = b / 4;
    const int e = b % 4;
    const int f = (b + 8) / 25;
    const int g = (b - f + 1) / 3;
    const int h = (19 * a + b - d - g + 15) % 30;
    const int i = c / 4;
    const int k = c % 4;
    const int l = mod(32 + 2 * e + 2 * i - h - k, 7);
    const int m = (a + 11 * h + 22 * l) / 451;
    const int month = (h + l - 7 * m + 114) / 31;
    const int day = ((h + l - 7 * m + 114) % 31) + 1;
    return {year, month, day};
}

std::vector<Date> holidays(int year) {
    std::vector<Date> h{
        observed({year, 1, 1}),          // New Year's
        nth_weekday(year, 1, 0, 3),      // MLK
        nth_weekday(year, 2, 0, 3),      // Presidents
        add_days(easter(year), -2),      // Good Friday
        last_weekday(year, 5, 0),        // Memorial
        observed({year, 7, 4}),          // Independence Day
        nth_weekday(year, 9, 0, 1),      // Labor
        nth_weekday(year, 11, 3, 4),     // Thanksgiving
        observed({year, 12, 25}),        // Christmas
    };
    if (year >= 2022) h.push_back(observed({year, 6, 19}));  // Juneteenth
    std::sort(h.begin(), h.end());
    h.erase(std::unique(h.begin(), h.end()), h.end());
    return h;
}

bool is_business_day(const Date& d) {
    if (weekday(d) >= 5) return false;
    return !contains(holidays(d.year), d);
}

bool is_early_close(const Date& d) {
    if (!is_business_day(d)) return false;
    if (d.month == 7 && d.day == 3) return weekday(d) <= 3;
    if (d.month == 11) return d == add_days(nth_weekday(d.year, 11, 3, 4), 1);
    return d.month == 12 && d.day == 24;
}

std::optional<Session> session_for(const Date& d) {
    if (!is_business_day(d)) return std::nullopt;
    return Session{{d, kOpen}, {d, is_early_close(d) ? kEarlyClose : kClose}};
}

Instant next_open(const Instant& after) {
    for (int offset = 0; offset < 14; ++offset) {
        const auto session = session_for(add_days(after.date, offset));
        if (session && session->open > after) return session->open;
    }
    throw std::runtime_error("no market open in the 14 days after " + iso(after.date) + ": calendar bug?");
}

Date previous_business_day(const Date& d) {
    Date cursor = add_days(d, -1);
    while (!is_business_day(cursor)) cursor = add_days(cursor, -1);
    return cursor;
}

Date last_completed_session(const Instant& at) {
    const auto session = session_for(at.date);
    if (session && at >= Instant{at.date, session->close.time + kBarSettle}) return at.date;
    return previous_business_day(at.date);
}

}  // namespace dv02::nyse
