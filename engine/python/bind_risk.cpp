#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/vector.h>

#include <string>
#include <utility>

#include "dv02/risk.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using namespace dv02::risk;

void bind_risk(nb::module_& m) {
    nb::enum_<AssetClass>(m, "AssetClass")
        .value("STOCK", AssetClass::Stock)
        .value("ETF", AssetClass::ETF)
        .value("FUTURE", AssetClass::Future)
        .value("OPTION", AssetClass::Option)
        .value("FUTURE_OPTION", AssetClass::FutureOption)
        .value("BOND", AssetClass::Bond)
        .value("FX", AssetClass::FX)
        .value("CRYPTO", AssetClass::Crypto)
        .value("OTHER", AssetClass::Other);

    m.def("asset_class_from_ibkr", &asset_class_from_ibkr, "sec_type"_a,
          "IBKR secType (STK, FUT, OPT, FOP, BOND, CASH, CRYPTO, ...) to AssetClass.");
    m.def("asset_class_name", [](AssetClass ac) { return std::string(to_string(ac)); }, "asset_class"_a);
    m.def("settles_in_cash", &settles_in_cash, "asset_class"_a);

    nb::class_<Position>(m, "Position")
        .def(
            "__init__",
            [](Position* self, std::string symbol, double quantity, double price, double multiplier, AssetClass ac,
               double exposure) { new (self) Position{std::move(symbol), quantity, price, multiplier, ac, exposure}; },
            "symbol"_a, "quantity"_a, "price"_a, "multiplier"_a = 1.0, "asset_class"_a = AssetClass::Stock,
            "exposure"_a = 1.0)
        .def_rw("symbol", &Position::symbol)
        .def_rw("quantity", &Position::quantity)
        .def_rw("price", &Position::price)
        .def_rw("multiplier", &Position::multiplier)
        .def_rw("asset_class", &Position::asset_class)
        .def_rw("exposure", &Position::exposure)
        .def_prop_ro("market_value", &Position::market_value)
        .def_prop_ro("notional", &Position::notional)
        .def("__repr__", [](const Position& p) {
            return "Position(" + p.symbol + ", " + std::to_string(p.quantity) + " @ " + std::to_string(p.price) + ")";
        });

    nb::class_<Order>(m, "Order")
        .def(
            "__init__",
            [](Order* self, std::string symbol, double quantity, double price, double multiplier, AssetClass ac,
               double commission, double exposure) {
                new (self) Order{std::move(symbol), quantity, price, multiplier, ac, commission, exposure};
            },
            "symbol"_a, "quantity"_a, "price"_a, "multiplier"_a = 1.0, "asset_class"_a = AssetClass::Stock,
            "commission"_a = 0.0, "exposure"_a = 1.0)
        .def_rw("symbol", &Order::symbol)
        .def_rw("quantity", &Order::quantity)
        .def_rw("price", &Order::price)
        .def_rw("multiplier", &Order::multiplier)
        .def_rw("asset_class", &Order::asset_class)
        .def_rw("commission", &Order::commission)
        .def_rw("exposure", &Order::exposure)
        .def("__repr__", [](const Order& o) {
            return "Order(" + o.symbol + ", " + std::to_string(o.quantity) + " @ " + std::to_string(o.price) + ")";
        });

    nb::class_<Book>(m, "Book")
        .def(
            "__init__",
            [](Book* self, double nav, double cash, std::vector<Position> positions) {
                new (self) Book{nav, cash, std::move(positions)};
            },
            "nav"_a, "cash"_a, "positions"_a = std::vector<Position>{})
        .def_rw("nav", &Book::nav)
        .def_rw("cash", &Book::cash)
        .def_rw("positions", &Book::positions);

    nb::class_<Policy>(m, "Policy")
        .def(
            "__init__",
            [](Policy* self, double max_gross, double min_cash, double max_net, double min_net, double max_single_name) {
                new (self) Policy{max_gross, min_cash, max_net, min_net, max_single_name};
            },
            "max_gross"_a = 1.0, "min_cash"_a = 0.0, "max_net"_a = 1.0, "min_net"_a = -1.0,
            "max_single_name"_a = 0.25)
        .def_rw("max_gross", &Policy::max_gross)
        .def_rw("min_cash", &Policy::min_cash)
        .def_rw("max_net", &Policy::max_net)
        .def_rw("min_net", &Policy::min_net)
        .def_rw("max_single_name", &Policy::max_single_name);

    nb::class_<Exposure>(m, "Exposure")
        .def_ro("nav", &Exposure::nav)
        .def_ro("cash", &Exposure::cash)
        .def_ro("long_value", &Exposure::long_value)
        .def_ro("short_value", &Exposure::short_value)
        .def_ro("gross", &Exposure::gross)
        .def_ro("net", &Exposure::net)
        .def_ro("cash_ratio", &Exposure::cash_ratio)
        .def_ro("largest_name", &Exposure::largest_name)
        .def_ro("largest_weight", &Exposure::largest_weight)
        .def_ro("positions", &Exposure::positions);

    nb::class_<Breach>(m, "Breach")
        .def_ro("rule", &Breach::rule)
        .def_ro("blocking", &Breach::blocking)
        .def_ro("value", &Breach::value)
        .def_ro("limit", &Breach::limit)
        .def_ro("message", &Breach::message)
        .def("__repr__", [](const Breach& b) {
            return std::string(b.blocking ? "Breach(BLOCK " : "Breach(warn ") + b.rule + ": " + b.message + ")";
        });

    nb::class_<Result>(m, "Result")
        .def_ro("ok", &Result::ok)
        .def_ro("reduce_only", &Result::reduce_only)
        .def_ro("before", &Result::before)
        .def_ro("after", &Result::after)
        .def_ro("breaches", &Result::breaches)
        .def_ro("buy_capacity", &Result::buy_capacity);

    m.def("exposure", &exposure, "book"_a);
    m.def("apply", &apply, "book"_a, "orders"_a);
    m.def("check", &check, "book"_a, "orders"_a, "policy"_a = Policy{},
          "Whether the book after these orders stays inside the policy. Every holding counts; anything "
          "unpriced blocks.");
}
