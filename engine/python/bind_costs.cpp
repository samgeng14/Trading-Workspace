// dv02_engine._core.costs: the cost model (engine/src/costs.cpp). The
// Python face is python/dv02_engine/costs.py.
#include <nanobind/nanobind.h>
#include <nanobind/stl/map.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/vector.h>

#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "dv02/costs.hpp"

namespace nb = nanobind;
using namespace nb::literals;
namespace c = dv02::costs;

namespace {

using RootMap = std::map<std::string, c::FuturesRoot, std::less<>>;
using InstrumentMap = std::map<std::string, c::Instrument, std::less<>>;

std::uint16_t month_mask(const std::vector<int>& months) {
    if (months.empty()) throw std::invalid_argument("roll_months must name at least one month");
    std::uint16_t mask = 0;
    for (const int month : months) {
        if (month < 1 || month > 12) throw std::invalid_argument("roll months are 1-12, not " + std::to_string(month));
        mask = static_cast<std::uint16_t>(mask | c::month_bit(static_cast<unsigned>(month)));
    }
    return mask;
}

std::vector<int> month_list(std::uint16_t mask) {
    std::vector<int> months;
    for (int month = 1; month <= 12; ++month)
        if (mask & c::month_bit(static_cast<unsigned>(month))) months.push_back(month);
    return months;
}

}  // namespace

void bind_costs(nb::module_& m) {
    nb::module_ cm = m.def_submodule("costs", "Trading and carrying costs (engine/src/costs.cpp)");

    nb::enum_<c::Kind>(cm, "Kind").value("EQUITY", c::Kind::Equity).value("FUTURE", c::Kind::Future);

    nb::class_<c::Instrument>(cm, "Instrument")
        .def(
            "__init__",
            [](c::Instrument* self, c::Kind kind, std::string root, double multiplier) {
                new (self) c::Instrument{kind, std::move(root), multiplier};
            },
            "kind"_a = c::Kind::Equity, "root"_a = "", "multiplier"_a = 1.0)
        .def_ro("kind", &c::Instrument::kind)
        .def_ro("root", &c::Instrument::root)
        .def_ro("multiplier", &c::Instrument::multiplier)
        .def("__repr__", [](const c::Instrument& i) {
            return nb::str("Instrument(kind={}, root={!r}, multiplier={!r})")
                .format(nb::str(nb::cast(i.kind)), i.root, i.multiplier);
        });

    nb::class_<c::FuturesRoot>(cm, "FuturesRoot")
        .def(
            "__init__",
            [](c::FuturesRoot* self, double commission, double roll_bps, const std::vector<int>& roll_months,
               int roll_day) {
                if (roll_day < 1 || roll_day > 31)
                    throw std::invalid_argument("roll_day must be 1-31, not " + std::to_string(roll_day));
                new (self) c::FuturesRoot{commission, roll_bps, month_mask(roll_months), static_cast<unsigned>(roll_day)};
            },
            "commission"_a, "roll_bps"_a = 1.0, "roll_months"_a = std::vector<int>{3, 6, 9, 12}, "roll_day"_a = 10,
            "Per side, per contract, all-in commission in dollars; the calendar-spread cost of a roll in bps of "
            "notional; the months a held contract rolls (1-12); and the day of the month on or after which it rolls.")
        .def_ro("commission", &c::FuturesRoot::commission)
        .def_ro("roll_bps", &c::FuturesRoot::roll_bps)
        .def_prop_ro("roll_months", [](const c::FuturesRoot& r) { return month_list(r.roll_months); })
        .def_ro("roll_day", &c::FuturesRoot::roll_day)
        .def("__repr__", [](const c::FuturesRoot& r) {
            return nb::str("FuturesRoot(commission={!r}, roll_bps={!r}, roll_months={!r}, roll_day={!r})")
                .format(r.commission, r.roll_bps, month_list(r.roll_months), r.roll_day);
        });

    const c::CostModel defaults;
    nb::class_<c::CostModel>(cm, "CostModel")
        .def(
            "__init__",
            [](c::CostModel* self, bool commissions, double half_spread_bps, c::SymbolMap half_spread_bps_by_symbol,
               double impact_k, std::size_t impact_lookback, double borrow_bps, c::SymbolMap borrow_bps_by_symbol,
               double cash_interest_bps, const RootMap& futures, InstrumentMap instruments) {
                c::CostModel model;
                model.commissions = commissions;
                model.half_spread_bps = half_spread_bps;
                model.half_spread_bps_by_symbol = std::move(half_spread_bps_by_symbol);
                model.impact_k = impact_k;
                model.impact_lookback = impact_lookback;
                model.borrow_bps = borrow_bps;
                model.borrow_bps_by_symbol = std::move(borrow_bps_by_symbol);
                model.cash_interest_bps = cash_interest_bps;
                for (const auto& [root, spec] : futures) model.futures.insert_or_assign(root, spec);  // over the defaults
                model.instruments = std::move(instruments);
                c::validate(model);
                new (self) c::CostModel(std::move(model));
            },
            nb::kw_only(), "commissions"_a = defaults.commissions, "half_spread_bps"_a = defaults.half_spread_bps,
            "half_spread_bps_by_symbol"_a = c::SymbolMap{}, "impact_k"_a = defaults.impact_k,
            "impact_lookback"_a = defaults.impact_lookback, "borrow_bps"_a = defaults.borrow_bps,
            "borrow_bps_by_symbol"_a = c::SymbolMap{}, "cash_interest_bps"_a = defaults.cash_interest_bps,
            "futures"_a = RootMap{}, "instruments"_a = InstrumentMap{},
            "The live book's costs. futures entries are added to, or replace, the default table by root. Raises "
            "ValueError for an invalid model.")
        .def_ro("commissions", &c::CostModel::commissions)
        .def_ro("half_spread_bps", &c::CostModel::half_spread_bps)
        .def_ro("half_spread_bps_by_symbol", &c::CostModel::half_spread_bps_by_symbol)
        .def_ro("impact_k", &c::CostModel::impact_k)
        .def_ro("impact_lookback", &c::CostModel::impact_lookback)
        .def_ro("borrow_bps", &c::CostModel::borrow_bps)
        .def_ro("borrow_bps_by_symbol", &c::CostModel::borrow_bps_by_symbol)
        .def_ro("cash_interest_bps", &c::CostModel::cash_interest_bps)
        .def_ro("futures", &c::CostModel::futures)
        .def_ro("instruments", &c::CostModel::instruments)
        .def("half_spread_for", &c::CostModel::half_spread_for, "symbol"_a)
        .def("borrow_for", &c::CostModel::borrow_for, "symbol"_a)
        .def("instrument_for", &c::CostModel::instrument_for, "symbol"_a)
        .def("__repr__", [](const c::CostModel& x) {
            return nb::str("CostModel(commissions={!r}, half_spread_bps={!r}, impact_k={!r}, impact_lookback={!r}, "
                           "borrow_bps={!r}, cash_interest_bps={!r}, {} spread overrides, {} borrow overrides, "
                           "{} futures roots, {} instruments)")
                .format(x.commissions, x.half_spread_bps, x.impact_k, x.impact_lookback, x.borrow_bps,
                        x.cash_interest_bps, x.half_spread_bps_by_symbol.size(), x.borrow_bps_by_symbol.size(),
                        x.futures.size(), x.instruments.size());
        });

    cm.def("default_futures", &c::default_futures, "The built-in futures table, by root.");
    cm.def("ibkr_pro_fixed", &c::ibkr_pro_fixed, "trade_value"_a, "price"_a);
    cm.def("futures_commission", &c::futures_commission, "trade_value"_a, "price"_a, "multiplier"_a, "per_contract"_a);
    cm.def("spread_cost", &c::spread_cost, "trade_value"_a, "half_spread_bps"_a);
    cm.def("impact_bps", &c::impact_bps, "trade_value"_a, "daily_vol"_a, "adv_value"_a, "k"_a);
    cm.def("impact_cost", &c::impact_cost, "trade_value"_a, "daily_vol"_a, "adv_value"_a, "k"_a);
    cm.def("borrow_cost", &c::borrow_cost, "short_value"_a, "annual_bps"_a, "days"_a);
    cm.def("cash_interest", &c::cash_interest, "cash"_a, "annual_bps"_a, "days"_a);
    cm.def("roll_cost", &c::roll_cost, "notional"_a, "price"_a, "multiplier"_a, "roll_bps"_a, "per_contract"_a);
    cm.def(
        "daily_volatility", [](const std::vector<double>& closes) { return c::daily_volatility(closes); }, "closes"_a);
    cm.def(
        "average_dollar_volume",
        [](const std::vector<double>& closes, const std::vector<double>& volumes, double multiplier) {
            return c::average_dollar_volume(closes, volumes, multiplier);
        },
        "closes"_a, "volumes"_a, "multiplier"_a = 1.0);
}
