"""The cvxpy solver choice (src/trading_engine/solvers.py) and the optimizers that use it."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import cvxpy as cp
import numpy as np
import polars as pl
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trading_engine import solvers  # noqa: E402
from trading_engine.aggregators.catalogue.mvo_aggregator import _solve_mvo  # noqa: E402
from trading_engine.optimizers.catalogue.mean_variance import (  # noqa: E402
    MeanVarianceOptimizer,
    _solve_mv,
)
from trading_engine.optimizers.catalogue.miqp_mean_variance import (  # noqa: E402
    MIQPMeanVarianceOptimizer,
    _solve_mv_miqp,
)

N = 5
# finite bounds that bind: the first asset's target (0.6) is above its 0.3 cap
W_MIN = np.zeros(N)
W_MAX = np.array([0.3, 0.4, 0.4, 0.4, 0.4])
TARGET = np.array([0.6, 0.1, 0.1, 0.1, 0.1])


def _cov() -> np.ndarray:
    returns = np.random.default_rng(7).normal(0.0, 0.01, size=(120, N))
    return np.cov(returns.T)


def _sample_cov(window: np.ndarray) -> np.ndarray:
    return np.cov(window.T) + 1e-6 * np.eye(window.shape[1])


@pytest.fixture(autouse=True)
def fresh_choice(monkeypatch):
    """Every test decides from scratch: no cached MOSEK check, no DV02_SOLVER."""
    cached = (solvers.mosek_usable, solvers._installed_solvers)
    monkeypatch.delenv(solvers.SOLVER_ENV, raising=False)
    for fn in cached:
        fn.cache_clear()
    yield
    for fn in cached:  # tests may have replaced the module attributes
        fn.cache_clear()


@pytest.fixture
def no_mosek(monkeypatch):
    """``import mosek`` fails, as on a machine without the package."""
    monkeypatch.setitem(sys.modules, "mosek", None)


@pytest.fixture
def solves(monkeypatch) -> list[tuple[str | None, str]]:
    """Every cvxpy solve as (solver asked for, solver cvxpy used)."""
    calls: list[tuple[str | None, str]] = []
    original = cp.Problem.solve

    def recording_solve(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        calls.append((kwargs.get("solver"), self.solver_stats.solver_name))
        return result

    monkeypatch.setattr(cp.Problem, "solve", recording_solve)
    return calls


def _fake_mosek(*, license_ok: bool) -> tuple[ModuleType, list[str]]:
    """A stand-in mosek package whose license checkout passes or fails."""
    checkouts: list[str] = []

    class Error(Exception):
        pass

    class Env:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def checkoutlicense(self, feature):
            checkouts.append(feature)
            if not license_ok:
                raise Error("rescode.err_license_expired(1001)")

    module = ModuleType("mosek")
    module.Env = Env
    module.Error = Error
    module.feature = type("feature", (), {"pts": "pts"})
    return module, checkouts


# ── the choice ──────────────────────────────────────────────────────────────


def test_without_the_mosek_package_the_choice_is_clarabel(no_mosek):
    assert solvers.mosek_usable() is False
    assert solvers.pick_solver() == "CLARABEL"


def test_mosek_with_a_license_in_the_home_directory_is_chosen(monkeypatch, tmp_path):
    fake, checkouts = _fake_mosek(license_ok=True)
    monkeypatch.setitem(sys.modules, "mosek", fake)
    monkeypatch.delenv("MOSEKLM_LICENSE_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "mosek").mkdir()
    (tmp_path / "mosek" / "mosek.lic").write_text("license\n")

    assert solvers.pick_solver() == "MOSEK"
    assert solvers.pick_solver(mixed_integer=True) == "MOSEK"
    assert checkouts == ["pts"]  # checked once, then cached


def test_mosek_with_an_expired_license_falls_back_to_clarabel(monkeypatch, tmp_path):
    fake, checkouts = _fake_mosek(license_ok=False)
    monkeypatch.setitem(sys.modules, "mosek", fake)
    lic = tmp_path / "mosek.lic"
    lic.write_text("license\n")
    monkeypatch.setenv("MOSEKLM_LICENSE_FILE", str(lic))

    assert solvers.pick_solver() == "CLARABEL"
    assert checkouts == ["pts"]


def test_mosek_without_any_license_is_not_asked_for_one(monkeypatch, tmp_path):
    fake, checkouts = _fake_mosek(license_ok=True)
    monkeypatch.setitem(sys.modules, "mosek", fake)
    monkeypatch.delenv("MOSEKLM_LICENSE_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/mosek/mosek.lic

    assert solvers.pick_solver() == "CLARABEL"
    assert checkouts == []


def test_dv02_solver_overrides_the_choice(monkeypatch):
    monkeypatch.setenv(solvers.SOLVER_ENV, "scs")
    assert solvers.pick_solver() == "SCS"
    assert solvers.pick_solver(mixed_integer=True) == "SCS"


def test_dv02_solver_must_name_an_installed_solver(monkeypatch):
    monkeypatch.setenv(solvers.SOLVER_ENV, "NOT_A_SOLVER")
    with pytest.raises(ValueError, match="DV02_SOLVER=NOT_A_SOLVER is not an installed"):
        solvers.pick_solver()


@pytest.mark.parametrize(
    ("installed", "continuous", "mixed_integer"),
    [
        (("CLARABEL", "ECOS", "SCS"), "CLARABEL", None),
        (("ECOS", "SCS"), "ECOS", None),
        (("SCS", "OSQP"), "SCS", None),
        (("OSQP",), None, None),
        (("CLARABEL", "SCIP"), "CLARABEL", "SCIP"),
    ],
)
def test_free_solvers_in_order(monkeypatch, no_mosek, installed, continuous, mixed_integer):
    monkeypatch.setattr(solvers, "_installed_solvers", lambda: installed)
    assert solvers.pick_solver() == continuous
    assert solvers.pick_solver(mixed_integer=True) == mixed_integer


# ── the optimizers without MOSEK ────────────────────────────────────────────


def test_mean_variance_solves_with_clarabel_without_mosek(no_mosek, solves):
    w = _solve_mv(
        cov=_cov(), mu=TARGET, target_w=TARGET, gamma=1.0, lambda_te=1.0, kappa=1.0,
        w_prev=np.full(N, 0.2), turnover_lambda=0.1, w_min=W_MIN, w_max=W_MAX,
    )
    assert solves == [("CLARABEL", "CLARABEL")]
    assert np.all(w >= W_MIN - 1e-7) and np.all(w <= W_MAX + 1e-7)
    assert w[0] == pytest.approx(0.3, abs=1e-6)


def test_mvo_aggregator_solves_with_clarabel_without_mosek(no_mosek, solves):
    mu = np.array([0.02, 0.01, 0.0, -0.01, 0.005])
    long_only = _solve_mvo(
        _cov(), mu, kappa=1.0, turnover_lambda=0.0, prev_w=None, long_only=True,
        w_min=W_MIN, w_max=W_MAX,
    )
    long_short = _solve_mvo(
        _cov(), mu, kappa=1.0, turnover_lambda=0.1, prev_w=np.full(N, 0.2),
        long_only=False,
    )
    assert solves == [("CLARABEL", "CLARABEL"), ("CLARABEL", "CLARABEL")]
    assert long_only.sum() == pytest.approx(1.0)
    assert np.all(long_only >= 0.0) and np.all(long_only <= W_MAX + 1e-7)
    assert np.abs(long_short).sum() == pytest.approx(1.0)


def test_miqp_qp_path_solves_with_clarabel_without_mosek(no_mosek, solves):
    w = _solve_mv_miqp(
        cov=_cov(), mu=TARGET, target_w=TARGET, gamma=1.0, lambda_te=1.0, kappa=1.0,
        w_prev=np.full(N, 0.2), min_position_delta=0.0, w_min=W_MIN, w_max=W_MAX,
    )
    assert solves == [("CLARABEL", "CLARABEL")]
    assert w[0] == pytest.approx(0.3, abs=1e-6)


def test_miqp_without_a_mixed_integer_solver_drops_only_the_min_trade_rule(
    monkeypatch, no_mosek, solves
):
    monkeypatch.setattr(solvers, "MIXED_INTEGER_SOLVERS", ())
    kwargs = dict(
        cov=_cov(), mu=TARGET, target_w=TARGET, gamma=1.0, lambda_te=1.0, kappa=1.0,
        w_prev=np.full(N, 0.2), turnover_lambda=0.1, w_min=W_MIN, w_max=W_MAX,
    )
    with pytest.warns(UserWarning, match="No mixed-integer solver"):
        w = _solve_mv_miqp(min_position_delta=0.03, **kwargs)
    # the same problem as the plain QP: bounds and turnover penalty kept
    assert solves == [("CLARABEL", "CLARABEL")]
    np.testing.assert_allclose(w, _solve_mv_miqp(min_position_delta=0.0, **kwargs), atol=1e-8)
    assert w[0] == pytest.approx(0.3, abs=1e-6)


def _prices_and_targets(days: int = 40) -> tuple[pl.DataFrame, pl.DataFrame]:
    tickers = ["AAA", "BBB", "CCC"]
    rng = np.random.default_rng(3)
    dates = [f"2026-01-{d:02d}" if d <= 31 else f"2026-02-{d - 31:02d}" for d in range(1, days + 1)]
    prices = {"date": dates}
    for t in tickers:
        prices[t] = list(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, days))))
    targets = {"date": dates, "AAA": [0.6] * days, "BBB": [0.3] * days, "CCC": [0.1] * days}
    return pl.DataFrame(prices), pl.DataFrame(targets)


def test_optimizers_run_end_to_end_without_mosek(monkeypatch, no_mosek, solves):
    monkeypatch.setattr(solvers, "MIXED_INTEGER_SOLVERS", ())
    prices, targets = _prices_and_targets()
    bounds = {t: {"min": 0.0, "max": 0.4} for t in ("AAA", "BBB", "CCC")}
    for optimizer in (
        MeanVarianceOptimizer(cov_window_days=20, risk_model=_sample_cov, asset_weight_bounds=bounds),
        MIQPMeanVarianceOptimizer(
            cov_window_days=20, risk_model=_sample_cov, asset_weight_bounds=bounds,
            min_position_delta=0.03,
        ),
    ):
        out = optimizer(prices, targets).collect()
        solved = out.slice(19)  # rows before the covariance window pass targets through
        assert solved.height == 21
        assert solved["AAA"].max() <= 0.4 + 1e-6
        assert np.isfinite(solved.select("AAA", "BBB", "CCC").to_numpy()).all()
    assert solves and {used for _, used in solves} == {"CLARABEL"}


# ── MOSEK and CLARABEL agree (needs mosek and a license) ────────────────────


def test_clarabel_matches_mosek_on_a_small_problem(monkeypatch):
    if not solvers.mosek_usable():
        pytest.skip("needs the mosek package and a usable MOSEK license")
    mu = np.array([0.02, 0.01, 0.0, -0.01, 0.005])

    def solve_all(solver: str) -> list[np.ndarray]:
        monkeypatch.setenv(solvers.SOLVER_ENV, solver)
        return [
            _solve_mv(
                cov=_cov(), mu=TARGET, target_w=TARGET, gamma=1.0, lambda_te=1.0, kappa=1.0,
                w_prev=np.full(N, 0.2), turnover_lambda=0.1, w_min=W_MIN, w_max=W_MAX,
            ),
            _solve_mvo(
                _cov(), mu, kappa=1.0, turnover_lambda=0.1, prev_w=np.full(N, 0.2),
                long_only=True, w_min=W_MIN, w_max=W_MAX,
            ),
            _solve_mvo(_cov(), mu, kappa=1000.0, turnover_lambda=0.0, prev_w=None, long_only=False),
        ]

    # interior-point tolerances: within 0.01% of NAV per weight
    for mosek_w, clarabel_w in zip(solve_all("MOSEK"), solve_all("CLARABEL")):
        np.testing.assert_allclose(clarabel_w, mosek_w, atol=1e-4)
