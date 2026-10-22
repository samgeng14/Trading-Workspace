"""
shadow_h2.py — Shadow mode for H2, S0 with a trend overlay. It passed the
research gate on 2026-10-21 (research/s3a_vol_overlay/REPORT.md); PLAN §9 step 7
asks for at least three months of shadow before paper.

    python -m src.production.shadow_h2        # dv02-shadow.timer, 17:05 ET on weekdays; bin/tradeit shadow

Nothing is sent. Each evening it writes artifacts/shadow/h2/:
  * daily.csv: from the shadow's start, the trend sleeve's value (S1 long/flat,
    src/strategies/trend.py, run forward through the C++ walk-forward with the
    research's costs, lag 1 and monthly decisions), the live account's daily
    return (Flex NAV where published, else the API's close), and H2's: half
    each, as the research mixed them;
  * <session>.json: that session's numbers, the trend sleeve's latest decision,
    and what H2 would hold and the orders it would send if it replaced S0 now.
The sleeve's record is recomputed from prices each evening, so it is the rule's,
not a hand-kept book; each session's JSON keeps what was known that evening.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START = date(2026, 10, 22)  # the first session after H2 passed the gate
SLEEVE_SHARE = 0.5  # H2: half S0 (the live account), half the trend sleeve
PROFILE = "paper"


def _navs(path: Path) -> dict[date, float]:
    out: dict[date, float] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    out[date.fromisoformat(row["date"])] = float(row["nav"])
                except (KeyError, ValueError):
                    continue
    return out


def live_navs(root: Path, profile: str = PROFILE) -> pd.Series:
    """The account's NAV each session: Flex's statement where published, else the
    API's record of the close."""
    api = root / "artifacts" / "api"
    navs = {**_navs(api / f"nav_{profile}.csv"), **_navs(api / f"flex_nav_{profile}.csv")}
    return pd.Series({pd.Timestamp(d): v for d, v in sorted(navs.items())}, dtype=float)


def sleeve(panel: pd.DataFrame, volumes: pd.DataFrame, start: date) -> Any:
    """The trend sleeve from ``start``: the research's rule, costs and timing."""
    from dv02_engine.walkforward import walk_forward

    from src.strategies import trend

    first = int(panel.index.searchsorted(pd.Timestamp(start)))
    return walk_forward(panel, trend.trend_rule(long_flat=True), train=first, test=len(panel) - first,
                        costs=trend.costs(), n_trials=1, holdout=0, rebalance_every=trend.REBALANCE_EVERY,
                        lag=1, volumes=volumes, mandate=True)  # fmt: skip


def would_be_orders(targets: dict[str, float], positions: pd.DataFrame, nav: float) -> list[dict[str, Any]]:
    """The whole-share orders that would take the account from ``positions``
    (symbol, current_shares, market_price) to ``targets`` (weights of NAV)."""
    held = {str(r.symbol): (float(r.current_shares), float(r.market_price))
            for r in positions.itertuples() if float(r.market_price or 0) > 0}  # fmt: skip
    out = []
    for symbol in sorted(set(targets) | set(held)):
        shares, price = held.get(symbol, (0.0, None))
        if price is None:
            continue  # a target IBKR holds no mark for: priced at the next run
        delta = round(targets.get(symbol, 0.0) * nav / price - shares)
        if delta:
            out.append({"symbol": symbol, "action": "BUY" if delta > 0 else "SELL", "quantity": abs(delta),
                        "dollars": round(abs(delta) * price, 2)})  # fmt: skip
    return sorted(out, key=lambda o: -o["dollars"])


def run(now: datetime | None = None, root: Path = PROJECT_ROOT, start: date = START) -> dict[str, Any]:
    from src.strategies import trend

    now = now or datetime.now(timezone.utc)
    panel, volumes = trend.price_panel(root / "data" / "market" / "cleaned" / "prices")
    if panel.index[-1].date() <= start:
        return {"ok": True, "skipped": f"the shadow starts on {start}; prices run to {panel.index[-1].date()}"}
    wf = sleeve(panel, volumes, start)
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
    daily["h2_return"] = SLEEVE_SHARE * daily["sleeve_return"] + (1 - SLEEVE_SHARE) * daily["live_return"]
    both = daily["h2_return"].notna()
    daily["h2_value"] = (1 + daily["h2_return"].where(both, 0.0)).cumprod()
    daily["live_value"] = (1 + daily["live_return"].where(both, 0.0)).cumprod()

    decisions = wf.events
    last = decisions.iloc[-1] if len(decisions) else None
    sleeve_weights = ({s: float(last[s]) for s in decisions.columns if s not in ("decided", "executed", "fold")
                       and pd.notna(last[s]) and float(last[s]) != 0.0} if last is not None else {})  # fmt: skip
    s0 = pd.read_parquet(root / "data" / "market" / "cleaned" / "targets" / "etf_targets_monthly.parquet")
    s0 = s0[s0["rebalance_date"] == s0["rebalance_date"].max()]
    s0_weights = {str(r.symbol): float(r.target_weight) * trend.BUDGET for r in s0.itertuples()}
    h2 = {s: round(SLEEVE_SHARE * sleeve_weights.get(s, 0.0) + (1 - SLEEVE_SHARE) * s0_weights.get(s, 0.0), 6)
          for s in sorted(set(sleeve_weights) | set(s0_weights))}  # fmt: skip
    positions_path = root / "data" / "broker" / "positions" / f"{PROFILE}_positions_snapshot.csv"
    orders = []
    if positions_path.exists() and len(navs):
        orders = would_be_orders({k: v for k, v in h2.items() if v}, pd.read_csv(positions_path), float(navs.iloc[-1]))

    session = daily.index[-1].date()
    out_dir = root / "artifacts" / "shadow" / "h2"
    out_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(out_dir / "daily.csv", index_label="date")
    record = {
        "session": session.isoformat(),
        "recorded_at": now.isoformat(timespec="seconds"),
        "start": start.isoformat(),
        "sessions": int(both.sum()),
        "h2_value": float(daily["h2_value"].iloc[-1]),
        "live_value": float(daily["live_value"].iloc[-1]),
        "sleeve_value": float(daily["sleeve_value"].iloc[-1]),
        "sleeve_decision": {"decided": str(last["decided"])[:10], "executed": str(last["executed"])[:10],
                            "weights": sleeve_weights} if last is not None else None,  # fmt: skip
        "sleeve_costs": {k: float(wf.combined[k]) for k in ("total_cost", "turnover", "max_gross_exposure")},
        "h2_targets": h2,
        "would_be_orders": orders,
    }
    (out_dir / f"{session.isoformat()}.json").write_text(json.dumps(record, indent=2))
    return {"ok": True, **record}


def main(argv: list[str] | None = None) -> int:
    try:
        out = run()
    except Exception as exc:  # noqa: BLE001 — one line, and a failed unit for the alert
        print(f"shadow H2 failed: {type(exc).__name__}: {exc}")
        return 1
    if out.get("skipped"):
        print(out["skipped"])
        return 0
    print(f"H2 shadow, {out['sessions']} sessions since {out['start']} (through {out['session']}):")
    print(f"  H2 {out['h2_value'] - 1:+.2%}   live S0 {out['live_value'] - 1:+.2%}   trend sleeve {out['sleeve_value'] - 1:+.2%}")
    if out["sleeve_decision"]:
        d = out["sleeve_decision"]
        w = ", ".join(f"{s} {v:.0%}" for s, v in sorted(d["weights"].items(), key=lambda x: -x[1]))
        print(f"  trend sleeve, decided {d['decided']}: {w}")
    print(f"  would send {len(out['would_be_orders'])} orders to become H2 now (nothing is sent)")
    for o in out["would_be_orders"][:8]:
        print(f"    {o['action']:4} {o['quantity']:>6} {o['symbol']:5} ${o['dollars']:>12,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
