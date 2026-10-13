// dv02_engine._core: the C++ engine as seen from Python. Each area binds
// itself in its own file; python/dv02_engine/ wraps it in plain modules.
#include <nanobind/nanobind.h>

namespace nb = nanobind;

void bind_risk(nb::module_& m);
void bind_costs(nb::module_& m);
void bind_backtest(nb::module_& m);

NB_MODULE(_core, m) {
    m.doc() = "DV02 Fund C++ engine";
    m.attr("__version__") = "0.1.0";
    bind_risk(m);
    bind_costs(m);  // before backtest, which takes a costs.CostModel
    bind_backtest(m);
}
