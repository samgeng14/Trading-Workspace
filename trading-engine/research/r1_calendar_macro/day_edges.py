"""
day_edges.py — After R1's verdict, not part of the gate: what SPY earned, on
average, on the days the rules hold it, against its other days. It reads the
walk-forward's results (the daily returns, the primary's decisions) and the FOMC
calendar fetch.py cached, and writes results/post_hoc_day_edges.csv.

    python day_edges.py        # with the repo's .venv, after r1_calendar_macro.py

SPY's daily return over BIL's, in bps, from 2008-01-03 to 2023-10-11; Welch's t
for the difference of the two means.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FOMC = HERE.parent / "_data" / "fed" / "fomc_announcements.csv"


def compare(x: pd.Series, mask: pd.Series, within: pd.Series, label: str, period: str) -> dict:
    a, b = x[within & mask], x[within & ~mask]
    t = (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return {"days": label, "period": period, "bps_on_these": round(a.mean(), 2), "n_these": len(a),
            "bps_on_the_others": round(b.mean(), 2), "n_others": len(b), "welch_t": round(t, 2)}  # fmt: skip


def main() -> None:
    daily = pd.read_csv(RESULTS / "walkforward_daily_returns.csv", parse_dates=["date"]).set_index("date")
    decisions = pd.read_csv(RESULTS / "walkforward_primary_decisions.csv", parse_dates=["earns_session"])
    tom = set(decisions.loc[decisions["turn_of_month_day"].notna(), "earns_session"])
    fomc = set(pd.read_csv(FOMC, parse_dates=["date"])["date"])
    x = (daily["SPY"] - daily["BIL"]) * 1e4
    is_tom = pd.Series(x.index.isin(list(tom)), index=x.index)
    is_fomc = pd.Series(x.index.isin(list(fomc)), index=x.index) & ~is_tom
    every = pd.Series(True, index=x.index)
    early = pd.Series(x.index < "2016-01-01", index=x.index)
    rows = []
    for period, within in (("2008-2023", every), ("2008-2015", early), ("2016-2023", ~early)):
        rows.append(compare(x, is_tom, within, "turn of the month, against every other day", period))
    for period, within in (("2008-2023", every), ("2008-2015", early), ("2016-2023", ~early)):
        rows.append(compare(x, is_fomc, within & ~is_tom, "FOMC days outside it, against the other non-turn days", period))
    pd.DataFrame(rows).to_csv(RESULTS / "post_hoc_day_edges.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
