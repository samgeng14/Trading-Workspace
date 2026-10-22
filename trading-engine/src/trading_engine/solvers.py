"""
One solver choice for every cvxpy problem in trading_engine.

MOSEK is used only when the ``mosek`` package imports and a license checks
out. MOSEK finds the license itself: the file or server named by
MOSEKLM_LICENSE_FILE, else ~/mosek/mosek.lic. No license lives in this
repository.

Without MOSEK, continuous problems go to CLARABEL (installed with cvxpy),
then ECOS, then SCS. Mixed-integer problems go to SCIP if it is installed
(pip install pyscipopt); otherwise ``pick_solver(mixed_integer=True)``
returns None and the caller decides what to solve instead.

DV02_SOLVER=<name> (any installed cvxpy solver, e.g. CLARABEL or SCS)
overrides the choice for every problem.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import cvxpy as cp

logger = logging.getLogger(__name__)

SOLVER_ENV = "DV02_SOLVER"
CONTINUOUS_SOLVERS = ("CLARABEL", "ECOS", "SCS")
MIXED_INTEGER_SOLVERS = ("SCIP",)


@lru_cache(maxsize=1)
def _installed_solvers() -> tuple[str, ...]:
    return tuple(cp.installed_solvers())


def _license_configured() -> bool:
    """True when MOSEK has a license to look at: MOSEKLM_LICENSE_FILE or ~/mosek/mosek.lic."""
    if os.environ.get("MOSEKLM_LICENSE_FILE"):
        return True
    return (Path.home() / "mosek" / "mosek.lic").is_file()


@lru_cache(maxsize=1)
def mosek_usable() -> bool:
    """True when the mosek package imports and a license checks out."""
    try:
        import mosek
    except ImportError:
        return False
    if not _license_configured():
        return False
    try:
        with mosek.Env() as env:
            env.checkoutlicense(mosek.feature.pts)
    except Exception as exc:  # expired, malformed or unreachable license
        logger.warning("MOSEK license not usable, using a free solver: %s", exc)
        return False
    return True


def pick_solver(*, mixed_integer: bool = False) -> str | None:
    """
    The solver name to pass to ``cvxpy.Problem.solve(solver=...)``.

    :param mixed_integer: True for problems with boolean or integer variables.
    :return: A cvxpy solver name. None for a mixed-integer problem means no
             mixed-integer solver is available; for a continuous problem it
             means none of the preferred solvers is installed and cvxpy picks.
    """
    installed = _installed_solvers()
    override = os.environ.get(SOLVER_ENV, "").strip().upper()
    if override:
        if override not in installed:
            raise ValueError(
                f"{SOLVER_ENV}={override} is not an installed cvxpy solver "
                f"(installed: {', '.join(installed)})"
            )
        return override
    if mosek_usable():
        return "MOSEK"
    for name in MIXED_INTEGER_SOLVERS if mixed_integer else CONTINUOUS_SOLVERS:
        if name in installed:
            return name
    return None
