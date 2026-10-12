// dv02_engine._core.backtest: the backtester over numpy arrays. The frames
// (polars or pandas) are read and written in python/dv02_engine/backtest.py.
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/vector.h>

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "dv02/backtest.hpp"

namespace nb = nanobind;
using namespace nb::literals;
namespace bt = dv02::backtest;

namespace {

using Days = nb::ndarray<const std::int32_t, nb::ndim<1>, nb::c_contig, nb::device::cpu>;
using Matrix = nb::ndarray<const double, nb::ndim<2>, nb::c_contig, nb::device::cpu>;

// days since 1970-01-01, one per row; a dates x assets matrix; the asset names
bt::Table table(const Days& days, const Matrix& values, std::vector<std::string> assets, const char* what) {
    if (values.shape(0) != days.shape(0) || values.shape(1) != assets.size())
        throw std::invalid_argument(std::string(what) + ": " + std::to_string(days.shape(0)) + " dates and " +
                                    std::to_string(assets.size()) + " assets, but a " +
                                    std::to_string(values.shape(0)) + " x " + std::to_string(values.shape(1)) +
                                    " matrix");
    bt::Table t;
    t.dates.reserve(days.shape(0));
    for (std::size_t i = 0; i < days.shape(0); ++i) t.dates.push_back(bt::Date{std::chrono::days{days(i)}});
    t.assets = std::move(assets);
    t.values.assign(values.data(), values.data() + values.size());
    return bt::sort_by_date(std::move(t));
}

// A numpy array that owns the vector's data.
template <class T>
nb::object to_numpy(std::vector<T>&& v, std::initializer_list<std::size_t> shape) {
    auto* owned = new std::vector<T>(std::move(v));
    nb::capsule owner(owned, [](void* p) noexcept { delete static_cast<std::vector<T>*>(p); });
    return nb::cast(nb::ndarray<nb::numpy, T>(owned->data(), shape, owner));
}

nb::object to_numpy(std::vector<double>&& v) {
    const std::size_t n = v.size();
    return to_numpy(std::move(v), {n});
}

}  // namespace

void bind_backtest(nb::module_& m) {
    nb::module_ bm = m.def_submodule("backtest", "Portfolio backtester (engine/src/backtest.cpp)");

    nb::enum_<bt::FeeModel>(bm, "FeeModel")
        .value("NONE", bt::FeeModel::None)
        .value("IBKR_PRO_FIXED", bt::FeeModel::IbkrProFixed);
    bm.def("fee_model_from_string", &bt::fee_model_from_string, "name"_a);
    bm.def("ibkr_pro_fixed_commission", &bt::ibkr_pro_fixed_commission, "trade_value"_a, "price"_a);

    bm.def(
        "parse_dates",
        [](const std::vector<std::string>& texts) {
            std::vector<std::int32_t> days;
            days.reserve(texts.size());
            for (const bt::Date d : bt::parse_dates(texts))
                days.push_back(static_cast<std::int32_t>(d.time_since_epoch().count()));
            const std::size_t n = days.size();
            return to_numpy(std::move(days), {n});
        },
        "texts"_a,
        "Date strings to days since 1970-01-01 (int32). Raises ValueError naming the first bad row.");

    bm.def(
        "run",
        [](const Days& price_days, const Matrix& prices, std::vector<std::string> price_assets, const Days& weight_days,
           const Matrix& weights, std::vector<std::string> weight_assets, double initial_value, double slippage_bps,
           bt::FeeModel fee_model) {
            const bt::Table price_table = table(price_days, prices, std::move(price_assets), "prices");
            const bt::Table weight_table = table(weight_days, weights, std::move(weight_assets), "weights");
            bt::Settings settings;
            settings.initial_value = initial_value;
            settings.slippage_bps = slippage_bps;
            settings.fee_model = fee_model;

            bt::Result r;
            {
                nb::gil_scoped_release release;
                r = bt::run(price_table, weight_table, settings);
            }

            const std::size_t rows = r.dates.size(), cols = r.assets.size();
            std::vector<std::string> dates;
            dates.reserve(rows);
            for (const bt::Date d : r.dates) dates.push_back(bt::to_string(d));

            const bt::Metrics& mx = r.metrics;
            nb::dict metrics;  // the order of the metrics frame
            metrics["total_return"] = mx.total_return;
            metrics["annualized_return"] = mx.annualized_return;
            metrics["annualized_volatility"] = mx.annualized_volatility;
            metrics["sharpe_ratio"] = mx.sharpe_ratio;
            metrics["sortino_ratio"] = mx.sortino_ratio;
            metrics["max_drawdown"] = mx.max_drawdown;
            metrics["avg_drawdown"] = mx.avg_drawdown;
            metrics["avg_daily_return"] = mx.avg_daily_return;
            metrics["win_rate"] = mx.win_rate;
            metrics["num_trades"] = mx.num_trades;
            metrics["cumulative_volume_traded"] = mx.cumulative_volume_traded;
            metrics["portfolio_turnover"] = mx.portfolio_turnover;
            metrics["holding_period_years"] = mx.holding_period_years;
            metrics["cumulative_slippage_cost"] = mx.cumulative_slippage_cost;
            metrics["cumulative_commission_cost"] = mx.cumulative_commission_cost;

            nb::dict out;
            out["dates"] = std::move(dates);
            out["portfolio_value"] = to_numpy(std::move(r.portfolio_value));
            out["daily_return"] = to_numpy(std::move(r.daily_return));
            out["daily_log_return"] = to_numpy(std::move(r.daily_log_return));
            out["cumulative_return"] = to_numpy(std::move(r.cumulative_return));
            out["cumulative_log_return"] = to_numpy(std::move(r.cumulative_log_return));
            out["drawdown"] = to_numpy(std::move(r.drawdown));
            out["volume_traded"] = to_numpy(std::move(r.volume_traded));
            out["daily_slippage_cost"] = to_numpy(std::move(r.daily_slippage_cost));
            out["daily_commission_cost"] = to_numpy(std::move(r.daily_commission_cost));
            out["assets"] = std::move(r.assets);
            out["positions"] = to_numpy(std::move(r.positions), {rows, cols});
            out["cash"] = to_numpy(std::move(r.cash));
            out["weights"] = to_numpy(std::move(r.weights), {rows, cols});
            out["cash_weight"] = to_numpy(std::move(r.cash_weight));
            out["metrics"] = std::move(metrics);
            return out;
        },
        "price_days"_a, "prices"_a, "price_assets"_a, "weight_days"_a, "weights"_a, "weight_assets"_a,
        "initial_value"_a, "slippage_bps"_a, "fee_model"_a,
        "Run the backtest. Days are days since 1970-01-01 (parse_dates); the matrices are dates x assets, float64, "
        "C order, NaN for a missing price or an asset an event leaves out. Rows need not be sorted. Returns a dict "
        "of date strings, column arrays, the position and weight matrices, and the metrics.");
}
