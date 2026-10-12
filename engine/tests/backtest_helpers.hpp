// Helpers for the backtester tests, which follow the Rust hawk-backtester's
// tests case by case.
#pragma once

#include <algorithm>
#include <chrono>
#include <limits>
#include <string>
#include <utility>
#include <vector>

#include "dv02/backtest.hpp"

namespace dv02::backtest::test {

using Row = std::vector<std::pair<std::string, double>>;

inline constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

inline Date today() { return std::chrono::floor<std::chrono::days>(std::chrono::system_clock::now()); }

inline Date plus_days(Date d, int n) { return d + std::chrono::days{n}; }

// The Rust tests' make_price_data and make_weight_event: one row per date,
// assets in first-seen order, NaN where a row leaves an asset out.
inline Table table(const std::vector<std::pair<Date, Row>>& rows) {
    Table t;
    for (const auto& row : rows)
        for (const auto& entry : row.second)
            if (std::find(t.assets.begin(), t.assets.end(), entry.first) == t.assets.end())
                t.assets.push_back(entry.first);
    for (const auto& row : rows) {
        t.dates.push_back(row.first);
        std::vector<double> values(t.cols(), kNaN);
        for (const auto& entry : row.second) {
            const auto at = std::find(t.assets.begin(), t.assets.end(), entry.first) - t.assets.begin();
            values[static_cast<std::size_t>(at)] = entry.second;
        }
        t.values.insert(t.values.end(), values.begin(), values.end());
    }
    return t;
}

inline bool has_column(const Result& r, const std::string& asset) {
    return std::find(r.assets.begin(), r.assets.end(), asset) != r.assets.end();
}

// r.positions or r.weights at (row, asset); NaN when there is no such cell
inline double cell(const Result& r, const std::vector<double>& matrix, std::size_t row, const std::string& asset) {
    const auto at = std::find(r.assets.begin(), r.assets.end(), asset);
    if (at == r.assets.end() || row >= r.dates.size()) return kNaN;
    return matrix[row * r.assets.size() + static_cast<std::size_t>(at - r.assets.begin())];
}

}  // namespace dv02::backtest::test
