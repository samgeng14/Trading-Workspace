// Dates and tables for the backtester: the rules the Rust input handler applied.
#include <algorithm>
#include <cstdio>
#include <numeric>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

#include "dv02/backtest.hpp"

namespace dv02::backtest {
namespace {

// Rust's u8::from_str, which read months and days: an optional '+', then
// ASCII digits, at most 255.
std::optional<unsigned> parse_u8(std::string_view s) {
    if (!s.empty() && s.front() == '+') s.remove_prefix(1);
    if (s.empty()) return std::nullopt;
    unsigned v = 0;
    for (const char c : s) {
        if (c < '0' || c > '9') return std::nullopt;
        v = v * 10 + static_cast<unsigned>(c - '0');
        if (v > 255) return std::nullopt;
    }
    return v;
}

std::vector<std::string_view> split(std::string_view s, char sep) {
    std::vector<std::string_view> parts;
    for (std::size_t from = 0;;) {
        const auto at = s.find(sep, from);
        parts.push_back(s.substr(from, at == std::string_view::npos ? std::string_view::npos : at - from));
        if (at == std::string_view::npos) return parts;
        from = at + 1;
    }
}

std::invalid_argument bad(const char* what, std::size_t row, std::string_view text) {
    return std::invalid_argument(std::string(what) + " at row " + std::to_string(row) + ": " + std::string(text));
}

}  // namespace

Date parse_date(std::string_view text, std::size_t row) {
    // a date with any '-' is split on '-', otherwise on '/'
    const char sep = text.find('-') != std::string_view::npos   ? '-'
                     : text.find('/') != std::string_view::npos ? '/'
                                                                : '\0';
    if (sep == '\0') throw bad("Invalid date format", row, text);
    const auto parts = split(text, sep);
    if (parts.size() != 3) throw bad("Invalid date format", row, text);
    const auto month = parse_u8(parts[1]);
    if (!month) throw bad("Invalid month", row, parts[1]);
    const auto day = parse_u8(parts[2]);
    if (!day) throw bad("Invalid day", row, parts[2]);

    std::string_view year = parts[0];
    if (!year.empty() && year.front() == '+') year.remove_prefix(1);
    if (year.size() != 4 || !std::all_of(year.begin(), year.end(), [](char c) { return c >= '0' && c <= '9'; }))
        throw bad("Invalid date format", row, text);
    const int y = std::accumulate(year.begin(), year.end(), 0, [](int acc, char c) { return acc * 10 + (c - '0'); });

    const std::chrono::year_month_day ymd{std::chrono::year{y}, std::chrono::month{*month}, std::chrono::day{*day}};
    if (!ymd.ok()) throw bad("Invalid date format", row, text);
    return Date{ymd};
}

std::vector<Date> parse_dates(const std::vector<std::string>& texts) {
    std::vector<Date> dates;
    dates.reserve(texts.size());
    for (std::size_t i = 0; i < texts.size(); ++i) dates.push_back(parse_date(texts[i], i + 1));
    return dates;
}

std::string to_string(Date date) {
    const std::chrono::year_month_day ymd{date};
    char buf[32];
    std::snprintf(buf, sizeof buf, "%04d-%02u-%02u", static_cast<int>(ymd.year()), static_cast<unsigned>(ymd.month()),
                  static_cast<unsigned>(ymd.day()));
    return buf;
}

Table sort_by_date(Table table) {
    if (table.values.size() != table.rows() * table.cols())
        throw std::invalid_argument("table has " + std::to_string(table.values.size()) + " values for " +
                                    std::to_string(table.rows()) + " dates x " + std::to_string(table.cols()) +
                                    " assets");
    if (std::is_sorted(table.dates.begin(), table.dates.end())) return table;
    std::vector<std::size_t> order(table.rows());
    std::iota(order.begin(), order.end(), std::size_t{0});
    std::stable_sort(order.begin(), order.end(),
                     [&](std::size_t a, std::size_t b) { return table.dates[a] < table.dates[b]; });
    Table out;
    out.assets = std::move(table.assets);
    out.dates.reserve(order.size());
    out.values.reserve(table.values.size());
    const std::size_t cols = out.assets.size();
    for (const std::size_t r : order) {
        out.dates.push_back(table.dates[r]);
        const auto first = table.values.begin() + static_cast<std::ptrdiff_t>(r * cols);
        out.values.insert(out.values.end(), first, first + static_cast<std::ptrdiff_t>(cols));
    }
    return out;
}

Table parse_table(const std::vector<std::string>& dates, std::vector<std::string> assets, std::vector<double> values) {
    return sort_by_date(Table{parse_dates(dates), std::move(assets), std::move(values)});
}

}  // namespace dv02::backtest
