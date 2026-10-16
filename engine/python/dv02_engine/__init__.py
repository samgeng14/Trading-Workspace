"""DV02 Fund C++ engine, from Python.

    from dv02_engine import risk
    result = risk.check(risk.Book(nav, cash, positions), orders)

The math lives in C++ (engine/src); this package only re-exports it.
"""

from ._core import __version__

__all__ = ["__version__"]
