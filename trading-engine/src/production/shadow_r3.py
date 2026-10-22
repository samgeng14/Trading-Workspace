"""
shadow_r3.py — Shadow mode for R3, short the VIX futures premium. It passed the
research gate on 2026-10-22 (research/r3_vix_futures/REPORT.md, which also says
why the pass is fragile); PLAN §9 step 7 asks for at least three months of
shadow before anyone considers paper.

    python -m src.production.shadow_r3        # with H2's (src/production/shadows.py) at 17:05 ET; bin/tradeit shadow

Nothing is sent. Each evening it fetches Cboe's VX settlements and the VIX
(src/data/ingest/cboe_vx.py). Cboe publishes a session's settlement overnight,
so an evening's run holds the session before. It writes artifacts/shadow/r3/:
  * daily.csv: from the shadow's start, the sleeve's value (the rule in
    src/strategies/vix_premium.py run forward through the C++ walk-forward with
    the research's costs, lag 1, decided every session), the live account's
    daily return (Flex NAV where published, else the API's close), and the book
    of half each, as the research mixed S0 and the sleeve;
  * <session>.json: that session's numbers, the signal (the 30-day futures
    price against the VIX), the sleeve's decision, and the short it would hold
    at the live account's NAV, in VX and VXM contracts of the roll's two months.
The record is recomputed from Cboe's settlements each evening, so it is the
rule's, not a hand-kept book; each session's JSON keeps what was known then.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

START = date(2026, 10, 22)  # the session of the day R3 passed the gate
SHARE = 0.5  # the book: half the live account, half the sleeve
VX_MULTIPLIER, VXM_MULTIPLIER = 1000.0, 100.0  # dollars per VIX point: VX and the mini
# monthly VX settlements are 4 to 5 weeks apart: within 25 days of the earliest cached
# expiry, no uncached contract can be the front month
FRONT_KNOWN = pd.Timedelta(days=25)


def series(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """(the settlements table, the 30-day series, the VIX) from Cboe's cached files.

    The cache holds the contracts that expired within cboe_vx.HISTORY_DAYS and every
    one still listed, but each file goes back about nine months. Before the earliest
    cached expiry, the front month isn't in the cache, so the table starts
    FRONT_KNOWN days before it, where that expiry is the front.

    The roll's weights depend on how many trading days its period has, and the
    current period ends after the last settlement. Run on the settlements alone,
    the research's code squeezes that period into the days so far (it only ever
    mattered for its holdout's last month) and gives the last date no weight at
    all. So the table is extended with the NYSE sessions through the next
    expiries, empty, before the series is built: every date's weights are then the
    ones the full period gives, and stay put as later settlements arrive."""
    from src.common import clock
    from src.data.ingest import cboe_vx
    from src.strategies import vix_premium as vp

    table = vp.settles(vp.contracts(cboe_vx.contract_paths(root)))
    table = table[table.index >= min(table.columns) - FRONT_KNOWN]
    last = table.index[-1]
    ahead = [e for e in sorted(table.columns) if e > last][:3]
    if ahead:
        future = [d for d in pd.date_range(last + pd.Timedelta(days=1), max(ahead)) if clock.is_us_business_day(d.date())]
        cm = vp.cm30(table.reindex(table.index.append(pd.DatetimeIndex(future)))).loc[:last]
    else:
        cm = vp.cm30(table)
    return table, cm, vp.read_vix(cboe_vx.vix_path(root))


def price_panel(root: Path, cm: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes, volumes): BIL, chained as S1's panel chains it, and VXCM's level, from the
    first session the 30-day series covers. A session without a VX settlement carries
    VXCM's last level; a Cboe day that isn't a session folds into the next session's."""
    from src.strategies import trend
    from src.strategies import vix_premium as vp

    closes, volumes = trend.price_panel(root / "data" / "market" / "cleaned" / "prices")
    level = cm["er_level"].reindex(cm.index.union(closes.index)).ffill().reindex(closes.index)
    panel = pd.DataFrame({vp.CASH: closes[vp.CASH], vp.INSTRUMENT: level}).dropna()
    vol = pd.DataFrame({vp.CASH: volumes[vp.CASH].reindex(panel.index), vp.INSTRUMENT: float("nan")}, index=panel.index)
    return panel, vol


def sleeve(panel: pd.DataFrame, volumes: pd.DataFrame, cm: pd.DataFrame, vix: pd.Series, start: date) -> Any:
    """The sleeve from ``start``: the research's rule, costs and timing."""
    from dv02_engine.walkforward import walk_forward

    from src.strategies import vix_premium as vp

    first = int(panel.index.searchsorted(pd.Timestamp(start)))
    return walk_forward(panel, vp.rule(cm, vix), train=first, test=len(panel) - first, costs=vp.costs(), n_trials=1,
                        holdout=0, rebalance_every=1, lag=1, volumes=volumes, mandate=True)  # fmt: skip


def would_hold(cm: pd.DataFrame, table: pd.DataFrame, short: float, nav: float) -> list[dict[str, Any]]:
    """The short at ``nav`` (``short``, a share of NAV, negative) in the roll's two
    contracts at the latest settlement, as VX and VXM contracts."""
    t = cm.index[-1]
    row = cm.loc[t]
    if pd.isna(row["first"]) or not short:
        return []
    out = []
    for expiry, weight in ((row["first"], float(row["w_first"])), (row["second_roll"], 1.0 - float(row["w_first"]))):
        price = float(table.at[t, expiry])
        notional = short * nav * weight
        out.append({
            "expiry": pd.Timestamp(expiry).date().isoformat(),
            "weight": round(weight, 4),
            "settle": price,
            "notional": round(notional, 2),
            "vx": round(notional / (price * VX_MULTIPLIER), 2),
            "vxm": round(notional / (price * VXM_MULTIPLIER)),
        })  # fmt: skip
    return out


def run(now: datetime | None = None, root: Path = PROJECT_ROOT, start: date = START, fetch: bool = True) -> dict[str, Any]:
    from src.data.ingest import cboe_vx
    from src.production.shadow_h2 import live_navs
    from src.strategies import vix_premium as vp

    now = now or datetime.now(timezone.utc)
    fetched = cboe_vx.fetch(root) if fetch else None
    table, cm, vix = series(root)
    panel, volumes = price_panel(root, cm)
    if panel.index[-1].date() <= start:
        return {"ok": True, "skipped": f"the shadow starts on {start}; prices run to {panel.index[-1].date()}"}
    wf = sleeve(panel, volumes, cm, vix, start)
    equity = wf.equity["portfolio_value"]
    sleeve_r = wf.equity["daily_return"]
    navs = live_navs(root)
    live_r = navs.pct_change(fill_method=None).reindex(sleeve_r.index)
    daily = pd.DataFrame({
        "sleeve_value": equity / equity.iloc[0],
        "sleeve_return": sleeve_r,
        "live_nav": navs.reindex(sleeve_r.index),
        "live_return": live_r,
    })  # fmt: skip
    daily["book_return"] = SHARE * daily["sleeve_return"] + (1 - SHARE) * daily["live_return"]
    both = daily["book_return"].notna()
    daily["book_value"] = (1 + daily["book_return"].where(both, 0.0)).cumprod()
    daily["live_value"] = (1 + daily["live_return"].where(both, 0.0)).cumprod()

    last = wf.events.iloc[-1] if len(wf.events) else None
    weights = ({s: float(last[s]) for s in wf.events.columns if s not in ("decided", "executed", "fold")
                and pd.notna(last[s]) and float(last[s]) != 0.0} if last is not None else {})  # fmt: skip
    t = cm.index[-1]
    price, spot = float(cm.at[t, "cm30_price"]), vp.latest(vix, t)
    signal = {
        "date": t.date().isoformat(),
        "cm30_price": round(price, 4),
        "vix": spot,
        "premium": round(price / spot - 1.0, 4),
        "contango": price > spot,
        "front": pd.Timestamp(cm.at[t, "front"]).date().isoformat(),
        "second": pd.Timestamp(cm.at[t, "second"]).date().isoformat(),
    }  # fmt: skip
    short = -vp.NOTIONAL if signal["contango"] else 0.0  # what tonight's settlement decides for the next session
    holding = would_hold(cm, table, short, float(navs.iloc[-1])) if len(navs) else []

    session = daily.index[-1].date()
    out_dir = root / "artifacts" / "shadow" / "r3"
    out_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(out_dir / "daily.csv", index_label="date")
    record = {
        "session": session.isoformat(),
        "recorded_at": now.isoformat(timespec="seconds"),
        "start": start.isoformat(),
        "sessions": int(both.sum()),
        "book_value": float(daily["book_value"].iloc[-1]),
        "live_value": float(daily["live_value"].iloc[-1]),
        "sleeve_value": float(daily["sleeve_value"].iloc[-1]),
        "signal": signal,
        "sleeve_decision": {"decided": str(last["decided"])[:10], "executed": str(last["executed"])[:10],
                            "weights": weights} if last is not None else None,  # fmt: skip
        "would_hold": holding,
        "sleeve_costs": {k: float(wf.combined[k]) for k in ("total_cost", "turnover", "max_gross_exposure")},
        "fetched": fetched,
    }
    (out_dir / f"{session.isoformat()}.json").write_text(json.dumps(record, indent=2, default=str))
    return {"ok": True, **record}


def describe(out: dict[str, Any]) -> list[str]:
    s = out["signal"]
    lines = [
        f"R3 shadow, {out['sessions']} sessions since {out['start']} (through {out['session']}):",
        f"  book {out['book_value'] - 1:+.2%}   live S0 {out['live_value'] - 1:+.2%}   sleeve {out['sleeve_value'] - 1:+.2%}",
        f"  {s['date']}: 30-day futures {s['cm30_price']:.2f} against VIX {s['vix']:.2f} ({s['premium']:+.1%}): "
        + ("contango, so short the next session" if s["contango"] else "inverted, so flat the next session"),
    ]  # fmt: skip
    for h in out["would_hold"]:
        lines.append(f"    VX {h['expiry']}: {h['vxm']} VXM ({h['vx']} VX), ${h['notional']:,.0f} at {h['settle']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    try:
        out = run()
    except Exception as exc:  # noqa: BLE001 — one line, and a failed unit for the alert
        print(f"shadow R3 failed: {type(exc).__name__}: {exc}")
        return 1
    print(out["skipped"] if out.get("skipped") else "\n".join(describe(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
