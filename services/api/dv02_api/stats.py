"""Performance statistics on daily returns (fractions).

Same definitions as the dashboard's demo backend (apps/web/src/mocks/stats.ts).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

DAYS_PER_YEAR = 252


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: Sequence[float]) -> float:
    """Sample standard deviation."""
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def compound(rets: Sequence[float]) -> float:
    g = 1.0
    for r in rets:
        g *= 1 + r
    return g - 1


def annualized_return(rets: Sequence[float]) -> float:
    if not rets:
        return 0.0
    return (1 + compound(rets)) ** (DAYS_PER_YEAR / len(rets)) - 1


def annualized_vol(rets: Sequence[float]) -> float:
    return stdev(rets) * math.sqrt(DAYS_PER_YEAR)


def sharpe(rets: Sequence[float], rf_daily: float = 0.0) -> dict[str, float]:
    """Annualized Sharpe with a 95% interval from SE = sqrt((1 + SR^2/2) / T), T in years.

    A short record can't confirm a Sharpe: a year at SR 1 is roughly +/-2.4. With no
    returns the interval is reported as +/-10 rather than infinite (JSON has no inf).
    """
    years = len(rets) / DAYS_PER_YEAR
    excess = [r - rf_daily for r in rets]
    sd = stdev(excess)
    value = 0.0 if sd == 0 else mean(excess) / sd * math.sqrt(DAYS_PER_YEAR)
    se = math.sqrt((1 + value * value / 2) / years) if years > 0 else 10 / 1.96
    return {"value": value, "ci_low": value - 1.96 * se, "ci_high": value + 1.96 * se, "years": years}


def sortino(rets: Sequence[float], rf_daily: float = 0.0) -> float:
    excess = [r - rf_daily for r in rets]
    downside = math.sqrt(mean([min(r, 0.0) ** 2 for r in excess]))
    return 0.0 if downside == 0 else mean(excess) / downside * math.sqrt(DAYS_PER_YEAR)


def drawdowns(levels: Sequence[float]) -> list[float]:
    peak, out = -math.inf, []
    for v in levels:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def max_drawdown(levels: Sequence[float]) -> float:
    return min([0.0, *drawdowns(levels)])


def relative(port: Sequence[float], bench: Sequence[float]) -> dict[str, float]:
    n = min(len(port), len(bench))
    p, b = list(port[:n]), list(bench[:n])
    mp, mb = mean(p), mean(b)
    cov = sum((p[i] - mp) * (b[i] - mb) for i in range(n))
    var_b = sum((x - mb) ** 2 for x in b)
    beta = 0.0 if var_b == 0 else cov / var_b
    active = [p[i] - b[i] for i in range(n)]
    te = annualized_vol(active)
    return {
        "beta": beta,
        "alpha_ann": (mp - beta * mb) * DAYS_PER_YEAR,
        "tracking_error": te,
        "information_ratio": 0.0 if te == 0 else mean(active) * DAYS_PER_YEAR / te,
    }


def hit_rate(rets: Sequence[float]) -> float:
    return sum(r > 0 for r in rets) / len(rets) if rets else 0.0


def returns_of(levels: Sequence[float]) -> list[float]:
    return [levels[i] / levels[i - 1] - 1 if levels[i - 1] else 0.0 for i in range(1, len(levels))]
