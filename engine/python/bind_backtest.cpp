// dv02_engine._core.backtest: the backtester over numpy arrays. The frames
// (polars or pandas) are read and written in python/dv02_engine/backtest.py.
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/map.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/vector.h>

#include <cstdint>
#include <map>
#include <optional>
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

    nb::class_<bt::Mandate>(bm, "Mandate")
        .def(
            "__init__",
            [](bt::Mandate* self, double max_gross, double min_cash, std::map<std::string, double, std::less<>> exposure) {
                bt::Mandate mandate{max_gross, min_cash, std::move(exposure)};
                bt::validate(mandate);
                new (self) bt::Mandate(std::move(mandate));
            },
            nb::kw_only(), "max_gross"_a = 1.0, "min_cash"_a = 0.0,
            "exposure"_a = std::map<std::string, double, std::less<>>{},
            "The no-leverage mandate the backtest flags: gross (sum of |position x exposure| / value) at most "
            "max_gross, cash / value at least min_cash. exposure is per symbol, default 1 (TQQQ 3).")
        .def_ro("max_gross", &bt::Mandate::max_gross)
        .def_ro("min_cash", &bt::Mandate::min_cash)
        .def_ro("exposure", &bt::Mandate::exposure)
        .def("__repr__", [](const bt::Mandate& x) {
            return nb::str("Mandate(max_gross={!r}, min_cash={!r}, exposure={!r})")
                .format(x.max_gross, x.min_cash, nb::cast(x.exposure));
        });

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
           bt::FeeModel fee_model, std::optional<dv02::costs::CostModel> costs, std::optional<bt::Mandate> mandate,
           std::optional<Days> volume_days, std::optional<Matrix> volumes, std::vector<std::string> volume_assets,
           std::optional<std::int32_t> start_day) {
            const bt::Table price_table = table(price_days, prices, std::move(price_assets), "prices");
            const bt::Table weight_table = table(weight_days, weights, std::move(weight_assets), "weights");
            bt::Table volume_table;
            if (volume_days && volumes)
                volume_table = table(*volume_days, *volumes, std::move(volume_assets), "volumes");
            else if (volume_days || volumes || !volume_assets.empty())
                throw std::invalid_argument("volumes: pass volume_days, volumes and volume_assets together");
            bt::Settings settings;
            settings.initial_value = initial_value;
            settings.slippage_bps = slippage_bps;
            settings.fee_model = fee_model;
            settings.costs = std::move(costs);
            settings.mandate = std::move(mandate);
            if (start_day) settings.start_date = bt::Date{std::chrono::days{*start_day}};

            bt::Result r;
            {
                nb::gil_scoped_release release;
                r = bt::run(price_table, weight_table, volume_table, settings);
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
            if (settings.costs) {
                metrics["cumulative_spread_cost"] = mx.cumulative_spread_cost;
                metrics["cumulative_impact_cost"] = mx.cumulative_impact_cost;
                metrics["cumulative_borrow_cost"] = mx.cumulative_borrow_cost;
                metrics["cumulative_roll_cost"] = mx.cumulative_roll_cost;
                metrics["cumulative_interest_income"] = mx.cumulative_interest_income;
                metrics["cumulative_total_cost"] = mx.cumulative_total_cost;
                metrics["unmeasured_impact_trades"] = mx.unmeasured_impact_trades;
            }
            if (settings.mandate) {
                metrics["mandate_breach_days"] = mx.mandate_breach_days;
                metrics["max_gross_exposure"] = mx.max_gross_exposure;
                metrics["min_mandate_cash"] = mx.min_mandate_cash;
            }

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
            if (settings.costs) {
                out["daily_spread_cost"] = to_numpy(std::move(r.daily_spread_cost));
                out["daily_impact_cost"] = to_numpy(std::move(r.daily_impact_cost));
                out["daily_borrow_cost"] = to_numpy(std::move(r.daily_borrow_cost));
                out["daily_roll_cost"] = to_numpy(std::move(r.daily_roll_cost));
                out["daily_interest_income"] = to_numpy(std::move(r.daily_interest_income));
                out["daily_total_cost"] = to_numpy(std::move(r.daily_total_cost));
            }
            if (settings.mandate) {
                out["gross_exposure"] = to_numpy(std::move(r.gross_exposure));
                out["mandate_cash"] = to_numpy(std::move(r.mandate_cash));
                const std::size_t n = r.mandate_breach.size();
                out["mandate_breach"] = to_numpy(std::move(r.mandate_breach), {n});
            }
            out["assets"] = std::move(r.assets);
            out["positions"] = to_numpy(std::move(r.positions), {rows, cols});
            out["cash"] = to_numpy(std::move(r.cash));
            out["weights"] = to_numpy(std::move(r.weights), {rows, cols});
            out["cash_weight"] = to_numpy(std::move(r.cash_weight));
            out["metrics"] = std::move(metrics);
            return out;
        },
        "price_days"_a, "prices"_a, "price_assets"_a, "weight_days"_a, "weights"_a, "weight_assets"_a,
        "initial_value"_a, "slippage_bps"_a, "fee_model"_a, nb::kw_only(), "costs"_a = nb::none(),
        "mandate"_a = nb::none(), "volume_days"_a = nb::none(), "volumes"_a = nb::none(),
        "volume_assets"_a = std::vector<std::string>{}, "start_day"_a = nb::none(),
        "Run the backtest. Days are days since 1970-01-01 (parse_dates); the matrices are dates x assets, float64, "
        "C order, NaN for a missing price, volume or an asset an event leaves out. Rows need not be sorted. costs "
        "(a costs.CostModel) replaces slippage_bps and fee_model; volumes (shares or contracts a day) feed its "
        "market impact; mandate (a Mandate) flags the days outside it; start_day (days since 1970-01-01) starts "
        "the backtest there instead of on the first weight date. Returns a dict of date strings, column "
        "arrays, the position and weight matrices, and the metrics, with the cost and mandate columns and metrics "
        "when those are set.");
}
