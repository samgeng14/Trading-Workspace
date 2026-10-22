"""Performance and summary numbers from a daily NAV series and benchmark closes."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import stats

RANGES = {"1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365}


def range_start(rng: str, first: date, last: date) -> date:
    if rng == "ITD":
        return first
    if rng == "YTD":
        prev_year_end = date(last.year - 1, 12, 31)
        return max(first, prev_year_end)
    if rng == "MTD":
        prev_month_end = date(last.year, last.month, 1) - timedelta(days=1)
        return max(first, prev_month_end)
    return max(first, last - timedelta(days=RANGES[rng]))


def align(nav: list[tuple[date, float]], bench: dict[date, float]) -> list[tuple[date, float, float]]:
    """Portfolio NAV and benchmark level on the same dates; the benchmark carries forward over gaps."""
    out, last_b = [], None
    bench_dates = sorted(bench)
    j = 0
    for d, v in nav:
        while j < len(bench_dates) and bench_dates[j] <= d:
            last_b = bench[bench_dates[j]]
            j += 1
        if last_b is not None:
            out.append((d, v, last_b))
    return out


def period_return(nav: list[tuple[date, float]], since: date, complete: bool) -> float | None:
    """Return from the last NAV on or before ``since`` to the latest NAV.

    When the history starts after ``since``, the return is only known if the
    history is complete (the account itself started then); otherwise None.
    """
    if not nav:
        return None
    base = next((v for d, v in reversed(nav) if d <= since), None)
    if base is None:
        if not complete:
            return None
        base = nav[0][1]
    return nav[-1][1] / base - 1 if base else None


def performance(
    nav: list[tuple[date, float]], bench: dict[date, float], rng: str, benchmark: dict[str, Any]
) -> dict[str, Any]:
    rows = align(nav, bench)
    if len(rows) < 2:
        raise ValueError("need at least two days of NAV history with benchmark prices")
    first, last = rows[0][0], rows[-1][0]
    start = range_start(rng, first, last)
    # The window starts at the last close on or before `start`, so its first
    # day's return is measured from the previous close.
    base_idx = max((i for i, r in enumerate(rows) if r[0] <= start), default=0)
    window = rows[base_idx:]
    p0, b0 = window[0][1], window[0][2]
    series = [
        {"date": d.isoformat(), "portfolio": 100 * v / p0, "benchmark": 100 * b / b0, "nav": v} for d, v, b in window
    ]
    levels = [v for _, v, _ in window]
    p_rets = stats.returns_of(levels)
    b_rets = stats.returns_of([b for _, _, b in window])
    dds = stats.drawdowns(levels)
    rel = stats.relative(p_rets, b_rets)
    n = len(p_rets)
    enough = n >= stats.MIN_RATIO_OBS
    year = n >= stats.DAYS_PER_YEAR
    days = [d for d, _, _ in window[1:]]
    best = max(range(len(p_rets)), key=lambda i: p_rets[i]) if p_rets else None
    worst = min(range(len(p_rets)), key=lambda i: p_rets[i]) if p_rets else None
    return {
        "range": rng,
        "start": window[0][0].isoformat(),
        "end": last.isoformat(),
        "benchmark": benchmark,
        "series": series,
        "drawdown": [{"date": d.isoformat(), "value": dd} for (d, _, _), dd in zip(window, dds, strict=False)],
        "stats": {
            "observations": n,
            "total_return": levels[-1] / levels[0] - 1,
            "benchmark_total_return": window[-1][2] / window[0][2] - 1,
            "ann_return": stats.annualized_return(p_rets) if year else None,
            "ann_vol": stats.annualized_vol(p_rets) if enough else None,
            "sharpe": stats.sharpe(p_rets) if enough else None,
            "max_drawdown": stats.max_drawdown(levels),
            "beta": rel["beta"] if enough else None,
            "alpha_ann": rel["alpha_ann"] if enough else None,
            "tracking_error": rel["tracking_error"] if enough else None,
            "information_ratio": rel["information_ratio"] if enough else None,
            "sortino": stats.sortino(p_rets) if enough else None,
            "hit_rate": stats.hit_rate(p_rets),
            "best_day": {"date": days[best].isoformat(), "value": p_rets[best]}
            if best is not None
            else {"date": last.isoformat(), "value": 0.0},
            "worst_day": {"date": days[worst].isoformat(), "value": p_rets[worst]}
            if worst is not None
            else {"date": last.isoformat(), "value": 0.0},
        },
        "monthly": monthly(rows),
    }


def monthly(rows: list[tuple[date, float, float]]) -> list[dict[str, Any]]:
    """Calendar-month returns since inception; the first month runs from the first NAV."""
    out: list[dict[str, Any]] = []
    prev_p, prev_b = rows[0][1], rows[0][2]
    for i, (d, v, b) in enumerate(rows):
        month_end = i == len(rows) - 1 or rows[i + 1][0].month != d.month
        if month_end:
            out.append({"year": d.year, "month": d.month, "portfolio": v / prev_p - 1, "benchmark": b / prev_b - 1})
            prev_p, prev_b = v, b
    return out
