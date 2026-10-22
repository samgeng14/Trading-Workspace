// dv02_engine._core.nyse and _core.oms: the NYSE calendar (engine/src/nyse.cpp)
// and the order manager's decisions (engine/src/oms.cpp). The Python faces,
// which take and give datetime dates and times, are python/dv02_engine/nyse.py
// and oms.py.
#include <nanobind/nanobind.h>
#include <nanobind/stl/map.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/vector.h>

#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "dv02/nyse.hpp"
#include "dv02/oms.hpp"

namespace nb = nanobind;
using namespace nb::literals;
namespace ny = dv02::nyse;
namespace om = dv02::oms;

namespace {

std::string side_name(om::Side side) { return std::string(om::to_string(side)); }

void bind_nyse(nb::module_& m) {
    nb::module_ nm = m.def_submodule("nyse", "The NYSE calendar in New York local time (engine/src/nyse.cpp)");

    nb::class_<ny::Date>(nm, "Date")
        .def(
            "__init__", [](ny::Date* self, int year, int month, int day) { new (self) ny::Date{year, month, day}; },
            "year"_a, "month"_a, "day"_a)
        .def_rw("year", &ny::Date::year)
        .def_rw("month", &ny::Date::month)
        .def_rw("day", &ny::Date::day)
        .def("__eq__", [](const ny::Date& a, const ny::Date& b) { return a == b; })
        .def("__lt__", [](const ny::Date& a, const ny::Date& b) { return a < b; })
        .def("__hash__", [](const ny::Date& d) { return ny::days_from_civil(d); })
        .def("__repr__", [](const ny::Date& d) { return "Date(" + ny::iso(d) + ")"; })
        .def("iso", [](const ny::Date& d) { return ny::iso(d); });

    nb::class_<ny::Instant>(nm, "Instant")
        .def(
            "__init__", [](ny::Instant* self, ny::Date date, ny::Micros time) { new (self) ny::Instant{date, time}; },
            "date"_a, "time"_a)
        .def_rw("date", &ny::Instant::date)
        .def_rw("time", &ny::Instant::time, "microseconds after local midnight")
        .def("__eq__", [](const ny::Instant& a, const ny::Instant& b) { return a == b; })
        .def("__lt__", [](const ny::Instant& a, const ny::Instant& b) { return a < b; });

    nb::class_<ny::Session>(nm, "Session").def_ro("open", &ny::Session::open).def_ro("close", &ny::Session::close);

    nm.def("weekday", &ny::weekday, "date"_a, "0 = Monday ... 6 = Sunday");
    nm.def("easter", &ny::easter, "year"_a);
    nm.def("holidays", &ny::holidays, "year"_a);
    nm.def("is_business_day", &ny::is_business_day, "date"_a);
    nm.def("is_early_close", &ny::is_early_close, "date"_a);
    nm.def("session_for", &ny::session_for, "date"_a);
    nm.def("next_open", &ny::next_open, "after"_a);
    nm.def("previous_business_day", &ny::previous_business_day, "date"_a);
    nm.def("last_completed_session", &ny::last_completed_session, "at"_a);
}

void bind_orders(nb::module_& m) {
    nb::module_ o = m.def_submodule("oms", "The order manager's decisions (engine/src/oms.cpp)");

    nb::class_<om::Holding>(o, "Holding")
        .def(
            "__init__",
            [](om::Holding* self, std::string symbol, double quantity, std::string sec_type, double market_price,
               double multiplier) {
                new (self) om::Holding{std::move(symbol), quantity, std::move(sec_type), market_price, multiplier};
            },
            "symbol"_a, "quantity"_a, "sec_type"_a = "", "market_price"_a = om::kNaN, "multiplier"_a = om::kNaN)
        .def_rw("symbol", &om::Holding::symbol)
        .def_rw("quantity", &om::Holding::quantity)
        .def_rw("sec_type", &om::Holding::sec_type)
        .def_rw("market_price", &om::Holding::market_price)
        .def_rw("multiplier", &om::Holding::multiplier);

    nb::class_<om::Target>(o, "Target")
        .def(
            "__init__",
            [](om::Target* self, std::string symbol, double weight, double close) {
                new (self) om::Target{std::move(symbol), weight, close};
            },
            "symbol"_a, "weight"_a, "close"_a = om::kNaN)
        .def_rw("symbol", &om::Target::symbol)
        .def_rw("weight", &om::Target::weight)
        .def_rw("close", &om::Target::close);

    nb::class_<om::Line>(o, "Line")
        .def_ro("symbol", &om::Line::symbol)
        .def_ro("in_model", &om::Line::in_model)
        .def_ro("close", &om::Line::close)
        .def_ro("price", &om::Line::price)
        .def_ro("target_weight", &om::Line::target_weight)
        .def_ro("target_dollars", &om::Line::target_dollars)
        .def_ro("target_shares", &om::Line::target_shares)
        .def_ro("current_shares", &om::Line::current_shares)
        .def_ro("delta_shares", &om::Line::delta_shares)
        .def_prop_ro("side", [](const om::Line& l) { return side_name(l.side); })
        .def_ro("abs_delta_shares", &om::Line::abs_delta_shares)
        .def_ro("trimmed_shares", &om::Line::trimmed_shares)
        .def_ro("estimated_trade_dollars", &om::Line::estimated_trade_dollars)
        .def_ro("estimated_commission", &om::Line::estimated_commission)
        .def_ro("tradable", &om::Line::tradable);

    nb::class_<om::PlannedOrder>(o, "PlannedOrder")
        .def_ro("symbol", &om::PlannedOrder::symbol)
        .def_prop_ro("side", [](const om::PlannedOrder& p) { return side_name(p.side); })
        .def_ro("quantity", &om::PlannedOrder::quantity)
        .def_ro("price", &om::PlannedOrder::price)
        .def_ro("estimated_trade_dollars", &om::PlannedOrder::estimated_trade_dollars)
        .def_ro("estimated_commission", &om::PlannedOrder::estimated_commission)
        .def_ro("in_model", &om::PlannedOrder::in_model)
        .def("__repr__", [](const om::PlannedOrder& p) {
            return "PlannedOrder(" + side_name(p.side) + " " + std::to_string(p.quantity) + " " + p.symbol + ")";
        });

    nb::class_<om::Basket>(o, "Basket")
        .def_ro("ok", &om::Basket::ok)
        .def_ro("error", &om::Basket::error)
        .def_ro("lines", &om::Basket::lines)
        .def_ro("orders", &om::Basket::orders)
        .def_ro("outside_sell", &om::Basket::outside_sell)
        .def_ro("outside_kept", &om::Basket::outside_kept)
        .def_ro("investable", &om::Basket::investable)
        .def_ro("trim_keep", &om::Basket::trim_keep)
        .def_ro("trimmed_dollars", &om::Basket::trimmed_dollars)
        .def_ro("warnings", &om::Basket::warnings);

    o.def(
        "plan_basket",
        [](std::vector<om::Target> targets, std::vector<om::Holding> holdings, double nav, double cash_buffer,
           double min_trade_dollars, std::vector<std::string> model, std::vector<std::string> whitelist,
           std::map<std::string, double> last_closes, std::optional<double> cash) {
            om::BasketInput in;
            in.targets = std::move(targets);
            in.holdings = std::move(holdings);
            in.nav = nav;
            in.cash_buffer = cash_buffer;
            in.min_trade_dollars = min_trade_dollars;
            in.model = std::move(model);
            in.whitelist = std::move(whitelist);
            in.last_closes = std::move(last_closes);
            in.cash = cash;
            return om::plan_basket(in);
        },
        "targets"_a, "holdings"_a, "nav"_a, "cash_buffer"_a = 0.005, "min_trade_dollars"_a = 1000.0,
        "model"_a = std::vector<std::string>{}, "whitelist"_a = std::vector<std::string>{},
        "last_closes"_a = std::map<std::string, double>{}, "cash"_a = nb::none(),
        "The reconciliation and the orders, from the latest targets and the account.");

    nb::class_<om::Weights>(o, "Weights").def_ro("weights", &om::Weights::weights).def_ro("unpriced", &om::Weights::unpriced);
    o.def("current_weights", &om::current_weights, "holdings"_a, "nav"_a, "fallback_prices"_a);
    o.def("gate_targets", &om::gate_targets, "targets"_a);

    nb::class_<om::DriftDecision>(o, "DriftDecision")
        .def_ro("rebalance", &om::DriftDecision::rebalance)
        .def_ro("reason", &om::DriftDecision::reason)
        .def_ro("max_drift", &om::DriftDecision::max_drift)
        .def_ro("drift_per_symbol", &om::DriftDecision::drift_per_symbol)
        .def_ro("composition_changed", &om::DriftDecision::composition_changed)
        .def_ro("in_cooldown", &om::DriftDecision::in_cooldown)
        .def_ro("cooldown_remaining_days", &om::DriftDecision::cooldown_remaining_days)
        .def_ro("outside_model", &om::DriftDecision::outside_model);

    o.def(
        "rebalance_decision",
        [](std::map<std::string, double> target_weights, std::map<std::string, double> current_weights, ny::Date today,
           std::optional<ny::Date> last_submitted_at, std::optional<std::map<std::string, double>> last_basket,
           double drift_threshold, double composition_threshold, int cooldown_days, bool force,
           std::vector<std::string> outside_model) {
            om::DriftInput in;
            in.target_weights = std::move(target_weights);
            in.current_weights = std::move(current_weights);
            in.today = today;
            in.last_submitted_at = last_submitted_at;
            in.last_basket = std::move(last_basket);
            in.drift_threshold = drift_threshold;
            in.composition_threshold = composition_threshold;
            in.cooldown_days = cooldown_days;
            in.force = force;
            in.outside_model = std::move(outside_model);
            return om::rebalance_decision(in);
        },
        "target_weights"_a, "current_weights"_a, "today"_a, "last_submitted_at"_a = nb::none(),
        "last_basket"_a = nb::none(), "drift_threshold"_a = 0.05, "composition_threshold"_a = 0.05,
        "cooldown_days"_a = 3, "force"_a = false, "outside_model"_a = std::vector<std::string>{},
        "Whether to rebalance today: the drift gate.");

    nb::class_<om::Execution>(o, "Execution")
        .def_ro("order_type", &om::Execution::order_type)
        .def_ro("tif", &om::Execution::tif)
        .def_prop_ro("auction", [](const om::Execution& e) { return e.auction == om::Auction::Close ? "close" : "open"; })
        .def_ro("session", &om::Execution::session)
        .def_ro("fills_at", &om::Execution::fills_at)
        .def_ro("moc_deadline", &om::Execution::moc_deadline);

    nb::class_<om::BookOrder>(o, "BookOrder")
        .def(
            "__init__",
            [](om::BookOrder* self, std::string symbol, double quantity, std::string status, std::string order_id,
               std::string perm_id, double price) {
                new (self) om::BookOrder{std::move(symbol), quantity,           std::move(status),
                                         std::move(order_id), std::move(perm_id), price};
            },
            "symbol"_a, "quantity"_a, "status"_a = "", "order_id"_a = "", "perm_id"_a = "", "price"_a = om::kNaN)
        .def_ro("symbol", &om::BookOrder::symbol)
        .def_ro("quantity", &om::BookOrder::quantity)
        .def_ro("status", &om::BookOrder::status)
        .def_ro("order_id", &om::BookOrder::order_id)
        .def_ro("perm_id", &om::BookOrder::perm_id)
        .def_ro("price", &om::BookOrder::price);

    nb::class_<om::Fill>(o, "Fill")
        .def(
            "__init__",
            [](om::Fill* self, std::string perm_id, std::string order_id, std::string symbol, double quantity) {
                new (self) om::Fill{std::move(perm_id), std::move(order_id), std::move(symbol), quantity};
            },
            "perm_id"_a, "order_id"_a, "symbol"_a, "quantity"_a);

    nb::class_<om::CountedOrder>(o, "CountedOrder")
        .def_ro("order", &om::CountedOrder::order)
        .def_ro("filled", &om::CountedOrder::filled)
        .def_ro("counted", &om::CountedOrder::counted);

    nb::class_<om::PositionBreak>(o, "PositionBreak")
        .def_ro("symbol", &om::PositionBreak::symbol)
        .def_ro("expected_qty", &om::PositionBreak::expected_qty)
        .def_ro("broker_qty", &om::PositionBreak::broker_qty)
        .def_ro("qty_diff", &om::PositionBreak::qty_diff)
        .def_ro("ref_price", &om::PositionBreak::ref_price)
        .def_ro("notional_diff_usd", &om::PositionBreak::notional_diff_usd);

    nb::class_<om::Reconcile>(o, "Reconcile")
        .def_ro("ok", &om::Reconcile::ok)
        .def_ro("error", &om::Reconcile::error)
        .def_ro("expected", &om::Reconcile::expected)
        .def_ro("actual", &om::Reconcile::actual)
        .def_ro("unit_values", &om::Reconcile::unit_values)
        .def_ro("orders", &om::Reconcile::orders)
        .def_ro("breaks", &om::Reconcile::breaks)
        .def_ro("material", &om::Reconcile::material);

    o.def(
        "reconcile",
        [](std::map<std::string, double> positions, std::map<std::string, double> unit_values,
           std::vector<om::BookOrder> orders, const std::vector<om::Holding>& holdings,
           const std::vector<om::Fill>& fills, double material_threshold_usd) {
            const om::ExpectedBook book{std::move(positions), std::move(unit_values), std::move(orders)};
            return om::reconcile(book, holdings, fills, material_threshold_usd);
        },
        "book_positions"_a, "book_unit_values"_a, "book_orders"_a, "holdings"_a, "fills"_a,
        "material_threshold_usd"_a = 100.0,
        "The expected book (its positions, unit values and orders) against IBKR's positions and fills.");

    o.def("moc_cutoff", &om::moc_cutoff, "exchange"_a, "microseconds before the close");
    o.def("moc_deadline", &om::moc_deadline, "close"_a, "exchanges"_a);
    o.def("execution_for", &om::execution_for, "exchanges"_a, "at"_a);
    o.def("pairwise_sum", &om::pairwise_sum, "values"_a);
    o.def("clean_symbol", &om::clean_symbol, "symbol"_a);
}

}  // namespace

void bind_oms(nb::module_& m) {
    bind_nyse(m);  // before oms, whose functions take its dates
    bind_orders(m);
}
