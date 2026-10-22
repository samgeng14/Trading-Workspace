"""
engine_shadow.py — The C++ engine's order manager, run beside the Python one
(docs/PLAN.md §6, migration step 2: the shadow OMS). Nothing it decides is sent.

Every run hands the engine (dv02_engine.oms, engine/src/oms.cpp) the inputs the
Python runner decided on and records whether the engine decided the same:

  * drift:  the rebalance gate, on every run (daily_runner._needs_rebalance_drift)
  * whatif: the basket the run would build now, from the positions and NAV the
            gate read and without the cash trim, on every run, rebalance or not
  * basket: the basket the run built, from the same inputs, cash included, on
            the days it rebalances (build_paper_basket)
  * reconcile: the run's first step, the expected book against IBKR's
            positions and fills: the same breaks, and the same verdict on
            whether one is material, which arms the kill switch
            (src/runtime/end_of_day_reconcile.py)
  * orders: each order sent, its status log replayed: the same last status,
            the same time to IBKR's acknowledgment, and the same reading of it
            (unacknowledged, still working, never placed), on the days orders go

The two baskets also compare the auction the batch would go to (market-on-close
or market-on-open), at the same instant for both.

Each comparison is a line of artifacts/engine_shadow/record.csv, with its details
in artifacts/engine_shadow/<session>/. The match is exact: the same orders, the
same shares on every line, the same dollars to the last bit. Anything that
differs is listed. A comparison that can't run (no engine, an error) is recorded
as not matching, never raised: the shadow must not touch the run. The engine
takes over sending only after STREAK_NEEDED sessions in a row match (PLAN §14),
and that switch is the owner's decision.

    python -m src.production.engine_shadow        # the streak and the latest differences
"""
from __future__ import annotations

import csv
import json
import math
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STREAK_NEEDED = 10  # sessions in a row with every comparison matching: two weeks
KINDS = ("reconcile", "drift", "whatif", "basket", "orders")
FIELDS = ["at", "session", "kind", "match", "python_orders", "engine_orders", "differences", "summary"]
INT_FIELDS = ("current_shares", "target_shares", "delta_shares", "abs_delta_shares", "trimmed_shares")
FLOAT_FIELDS = ("close", "price", "target_weight", "target_dollars", "estimated_trade_dollars", "estimated_commission")
MAX_LISTED = 40  # differences kept in a comparison's file


def shadow_dir(root: Path = PROJECT_ROOT) -> Path:
    return root / "artifacts" / "engine_shadow"


# ── inputs, as the engine takes them ─────────────────────────────────────


def _float(value: Any) -> float:
    """``value`` as a float; NaN when it isn't a number (None, "", text)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def holdings(positions: Any) -> list[Any]:
    """The positions frame (build_paper_basket.fetch_positions_df's columns) as
    engine Holdings. A missing quantity is 0, as ``row.get(...) or 0.0`` makes it in
    Python; a NaN one stays NaN, as it does there."""
    from dv02_engine import oms

    out = []
    if positions is None or len(positions) == 0:
        return out
    for row in positions.to_dict("records"):
        sec_type = row.get("sec_type")
        qty = row.get("current_shares")
        out.append(
            oms.Holding(
                str(row["symbol"]),
                0.0 if qty is None else _float(qty),
                sec_type if isinstance(sec_type, str) else "",
                _float(row.get("market_price")),
                _float(row.get("multiplier")),
            )
        )
    return out


def targets(latest: Any) -> list[Any]:
    """The latest targets frame (symbol, target_weight, close) as engine Targets."""
    from dv02_engine import oms

    has_close = "close" in latest.columns
    return [
        oms.Target(str(r["symbol"]), _float(r["target_weight"]), _float(r["close"]) if has_close else math.nan)
        for r in latest.to_dict("records")
    ]


# ── comparisons ──────────────────────────────────────────────────────────


def _same(a: Any, b: Any) -> bool:
    """Exactly equal, or both NaN."""
    a, b = _float(a), _float(b)
    return a == b or (math.isnan(a) and math.isnan(b))


def _shown(x: Any) -> Any:
    """A float for the JSON file: repr-exact, NaN as None."""
    if isinstance(x, float):
        return None if math.isnan(x) else x
    return x


class Differences(list):
    def add(self, what: str, python: Any, engine: Any) -> None:
        self.append({"what": what, "python": _shown(python), "engine": _shown(engine)})


def _orders_text(orders: Iterable[tuple[str, str, int]]) -> str:
    return "; ".join(f"{side} {qty} {symbol}" for symbol, side, qty in orders)


def compare_basket(recon: Any, orders_only: Any, basket: Any) -> dict[str, Any]:
    """Python's reconciliation and orders (build_reconciliation's two frames)
    against the engine's Basket: every line in order, then the orders in the
    order they are sent (submit_paper_orders.sells_first)."""
    from src.production.runtime.submit_paper_orders import sells_first

    diffs = Differences()
    py_lines = recon.to_dict("records")
    if len(py_lines) != len(basket.lines):
        diffs.add("lines", [str(r["symbol"]) for r in py_lines], [e.symbol for e in basket.lines])
    for i, (p, e) in enumerate(zip(py_lines, basket.lines)):
        where = f"line {i + 1} ({p['symbol']})"
        if str(p["symbol"]) != e.symbol:
            diffs.add(f"line {i + 1} symbol", str(p["symbol"]), e.symbol)
            continue
        if str(p["side"]) != e.side:
            diffs.add(f"{where} side", str(p["side"]), e.side)
        if bool(p.get("in_model", True)) != e.in_model:
            diffs.add(f"{where} in_model", bool(p.get("in_model", True)), e.in_model)
        for f in INT_FIELDS:
            if f in p and int(p[f]) != getattr(e, f):
                diffs.add(f"{where} {f}", int(p[f]), getattr(e, f))
        for f in FLOAT_FIELDS:
            if f in p and not _same(p[f], getattr(e, f)):
                diffs.add(f"{where} {f}", _float(p[f]), getattr(e, f))

    sent = sells_first(orders_only)
    py_orders = [(str(r["symbol"]), str(r["side"]), abs(int(r["delta_shares"]))) for r in sent.to_dict("records")]
    en_orders = [(o.symbol, o.side, int(o.quantity)) for o in basket.orders]
    if py_orders != en_orders:
        diffs.add("orders, in sending order", _orders_text(py_orders), _orders_text(en_orders))
    return {"differences": diffs, "python_orders": py_orders, "engine_orders": en_orders}


def compare_execution(symbols: Iterable[str], at: datetime) -> dict[str, Any]:
    """The auction a batch of ``symbols`` sent at ``at`` goes to: Python's
    (src/broker/ibkr/auction.execution_for) against the engine's."""
    from dv02_engine import oms

    from src.broker.ibkr.auction import execution_for
    from src.broker.ibkr.contracts import primary_exchange

    symbols = list(symbols)
    py = execution_for(symbols, at=at).as_dict()
    en = oms.execution_for([primary_exchange(s) for s in symbols], at)
    en = {
        "order_type": en["order_type"],
        "tif": en["tif"],
        "auction": en["auction"],
        "session": en["session"].isoformat(),
        "fills_at": en["fills_at"].isoformat(),
        "moc_deadline": en["moc_deadline"].isoformat() if en["moc_deadline"] else None,
    }
    diffs = Differences()
    for key, value in en.items():
        if py.get(key) != value:
            diffs.add(f"execution {key}", py.get(key), value)
    return {"differences": diffs, "python": py, "engine": en, "at": at.isoformat()}


def compare_drift(
    *,
    python_decision: Any,
    target_weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    latest: Any,
    positions: Any,
    nav: float,
    fallback_prices: Mapping[str, float],
    today: date,
    last_submitted_at: date | None,
    last_basket: Mapping[str, float] | None,
    rb_cfg: Mapping[str, Any],
    force: bool,
    outside_model: Iterable[str],
) -> dict[str, Any]:
    """The rebalance gate: the engine computes the weights from the same positions
    and prices, and decides from the same state, then each is compared with Python's."""
    from dv02_engine import oms

    diffs = Differences()
    engine_targets = oms.gate_targets(targets(latest))
    if dict(engine_targets) != dict(target_weights):
        diffs.add("target weights", dict(target_weights), dict(engine_targets))
    weights = oms.current_weights(holdings(positions), float(nav), dict(fallback_prices))
    for s in sorted(set(current_weights) | set(weights.weights)):
        if not _same(current_weights.get(s), weights.weights.get(s)):
            diffs.add(f"current weight {s}", current_weights.get(s), weights.weights.get(s))
    d = oms.rebalance_decision(
        target_weights, current_weights, today, last_submitted_at=last_submitted_at, last_basket=last_basket,
        drift_threshold=float(rb_cfg["drift_threshold"]), composition_threshold=float(rb_cfg["composition_threshold"]),
        cooldown_days=int(rb_cfg["cooldown_days"]), force=force, outside_model=list(outside_model),
    )  # fmt: skip
    for f in ("rebalance", "reason", "composition_changed", "in_cooldown", "cooldown_remaining_days"):
        if getattr(python_decision, f) != getattr(d, f):
            diffs.add(f, getattr(python_decision, f), getattr(d, f))
    if not _same(python_decision.max_drift, d.max_drift):
        diffs.add("max_drift", python_decision.max_drift, d.max_drift)
    py_drift = dict(python_decision.drift_per_symbol)
    for s in sorted(set(py_drift) | set(d.drift_per_symbol)):
        if not _same(py_drift.get(s), d.drift_per_symbol.get(s)):
            diffs.add(f"drift {s}", py_drift.get(s), d.drift_per_symbol.get(s))
    return {
        "differences": diffs,
        "python": {"rebalance": python_decision.rebalance, "reason": python_decision.reason},
        "engine": {"rebalance": d.rebalance, "reason": d.reason, "unpriced": list(weights.unpriced)},
    }


def compare_reconcile(
    *,
    book: Mapping[str, Any],
    positions: Any,
    fills: list[Mapping[str, Any]],
    threshold: float,
    expected: Mapping[str, float],
    orders: list[Mapping[str, Any]],
    breaks: list[Mapping[str, Any]],
    material: bool,
) -> dict[str, Any]:
    """The reconcile: Python's expected positions, counted orders, breaks and
    verdict (end_of_day_reconcile.reconcile_end_of_day) against the engine's from
    the same book, positions and fills."""
    from dv02_engine import oms

    from src.runtime.end_of_day_reconcile import _qty

    r = oms.reconcile(
        {str(k): float(v) for k, v in book["positions"].items()},
        {str(k): float(v) for k, v in (book.get("unit_values") or {}).items()},
        [oms.BookOrder(str(o["symbol"]), float(o["quantity"]), str(o.get("status") or ""), str(o.get("order_id") or ""),
                       str(o.get("perm_id") or ""), _float(o.get("price"))) for o in book["orders"]],
        holdings(positions),
        [oms.Fill(str(f.get("perm_id") or ""), str(f.get("order_id") or ""), str(f["symbol"]), float(f["quantity"]))
         for f in fills],
        float(threshold),
    )  # fmt: skip
    diffs = Differences()
    if not r.ok:
        diffs.add("the engine refused the positions", None, r.error)
        return {"differences": diffs}
    for s in sorted(set(expected) | set(r.expected)):
        if not _same(expected.get(s), r.expected.get(s)):
            diffs.add(f"expected {s}", expected.get(s), r.expected.get(s))
    if len(orders) != len(r.orders):
        diffs.add("orders counted", len(orders), len(r.orders))
    for i, (p, e) in enumerate(zip(orders, r.orders)):
        if not _same(p["filled"], e.filled):
            diffs.add(f"order {i + 1} ({p['symbol']}) filled", p["filled"], e.filled)
        if p["counted"] != e.counted:
            diffs.add(f"order {i + 1} ({p['symbol']}) counted", p["counted"], e.counted)
    if [b["symbol"] for b in breaks] != [b.symbol for b in r.breaks]:
        diffs.add("breaks", [b["symbol"] for b in breaks], [b.symbol for b in r.breaks])
    for p, e in zip(breaks, r.breaks):
        if p["symbol"] != e.symbol:
            continue
        engine = {"expected_qty": _qty(e.expected_qty), "broker_qty": _qty(e.broker_qty), "qty_diff": _qty(e.qty_diff),
                  "ref_price": e.ref_price, "notional_diff_usd": e.notional_diff_usd}  # fmt: skip
        for f, v in engine.items():
            if not (p[f] is None and v is None) and (p[f] is None or v is None or not _same(p[f], v)):
                diffs.add(f"break {p['symbol']} {f}", p[f], v)
    if bool(material) != r.material:
        diffs.add("material (arms the kill switch)", bool(material), r.material)
    n = len(breaks)
    return {"differences": diffs, "same": f"same {n} break{'s' if n != 1 else ''}" + (", material" if material else ""),
            "breaks": n, "material": bool(material)}  # fmt: skip


def compare_orders(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The submission log's rows (submit_paper_orders): for each order sent, what
    Python recorded against the engine's replay of its status log; for one never
    sent, whether both read it as never placed."""
    from dv02_engine import oms

    from src.broker.ibkr.orders import UNACKNOWLEDGED
    from src.production.runtime.submit_paper_orders import WORKING
    from src.runtime.end_of_day_reconcile import NOT_PLACED

    diffs = Differences()
    timelines = []
    for i, row in enumerate(rows):
        where = f"order {i + 1} ({row.get('action')} {row.get('quantity')} {row.get('symbol')})"
        status = str(row.get("status") or "")
        for name, py, en in (("unacknowledged", status in UNACKNOWLEDGED, oms.unacknowledged(status)),
                             ("working", status in WORKING, oms.working(status)),
                             ("never placed", status in NOT_PLACED, oms.never_placed(status))):  # fmt: skip
            if py != en:
                diffs.add(f"{where} {name}", py, en)
        raw = row.get("status_log")
        if not raw:
            continue  # never sent: nothing to replay
        log = json.loads(raw) if isinstance(raw, str) else list(raw)
        t = oms.replay([oms.StatusEvent(float(e["s"]), str(e["status"] or ""), int(e["code"] or 0), str(e["message"] or ""))
                        for e in log])  # fmt: skip
        if t.status != status:
            diffs.add(f"{where} status", status, t.status)
        recorded = row.get("acknowledged_s")
        recorded = None if recorded in (None, "") or _float(recorded) != _float(recorded) else _float(recorded)
        if recorded != t.acknowledged_s:
            diffs.add(f"{where} acknowledged after", recorded, t.acknowledged_s)
        timelines.append({"symbol": row.get("symbol"), "status": t.status, "acknowledged_s": t.acknowledged_s,
                          "messages": list(t.messages), "anomalies": list(t.anomalies)})  # fmt: skip
    n = len(rows)
    return {"differences": diffs, "same": f"same {n} order state{'s' if n != 1 else ''}", "timelines": timelines}


# ── the shadow, as the run calls it (never raises) ───────────────────────


def _session(at: datetime) -> str:
    from src.common import clock

    return at.astimezone(clock.NYSE_TZ).date().isoformat()


def record(kind: str, result: Mapping[str, Any], *, root: Path = PROJECT_ROOT, at: datetime | None = None) -> dict:
    """Write one comparison: its line in record.csv and its details. Returns the
    summary the run log keeps."""
    at = at or datetime.now(timezone.utc)
    session = _session(at)
    diffs = list(result.get("differences") or [])
    error = result.get("error")
    match = not diffs and not error
    py_orders = result.get("python_orders")
    en_orders = result.get("engine_orders")
    if error:
        summary = f"could not compare: {error}"
    elif match:
        summary = result.get("same") or ("same decision" if kind == "drift" else f"same {len(py_orders or [])} orders")
    else:
        summary = f"{len(diffs)} difference{'s' if len(diffs) != 1 else ''}, the first: {diffs[0]['what']}"
    out = {"kind": kind, "session": session, "match": match, "summary": summary}
    try:
        folder = shadow_dir(root) / session
        folder.mkdir(parents=True, exist_ok=True)
        detail = {**out, "at": at.isoformat(timespec="seconds"), **{k: v for k, v in result.items() if k != "differences"},
                  "differences": diffs[:MAX_LISTED], "differences_total": len(diffs)}  # fmt: skip
        stamp = at.astimezone(timezone.utc).strftime("%H%M%S")
        (folder / f"{stamp}_{kind}.json").write_text(json.dumps(detail, indent=2, default=str))
        path = shadow_dir(root) / "record.csv"
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow({
                "at": at.isoformat(timespec="seconds"), "session": session, "kind": kind, "match": int(match),
                "python_orders": "" if py_orders is None else len(py_orders),
                "engine_orders": "" if en_orders is None else len(en_orders),
                "differences": len(diffs), "summary": summary,
            })  # fmt: skip
    except OSError as exc:
        out["record_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _guarded(kind: str, root: Path, at: datetime | None, compare: Any) -> dict[str, Any]:
    try:
        result = compare()
    except ImportError as exc:
        result = {"error": f"the C++ engine (dv02_engine) isn't installed or is too old: {exc}"}
    except Exception as exc:  # noqa: BLE001 — a shadow that fails is recorded, never raised
        result = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=6)}
    try:
        return record(kind, result, root=root, at=at)
    except Exception as exc:  # noqa: BLE001
        return {"kind": kind, "match": False, "summary": f"could not record: {type(exc).__name__}: {exc}"}


def _plan(oms: Any, latest: Any, positions: Any, nav: float, **kw: Any) -> Any:
    return oms.plan_basket(targets(latest), holdings(positions), float(nav), **kw)


def basket(
    latest: Any,
    positions: Any,
    nav: float,
    *,
    cash_buffer: float,
    min_trade_dollars: float,
    symbol_whitelist: Iterable[str],
    model: Iterable[str],
    last_closes: Mapping[str, float],
    cash: float | None,
    recon: Any,
    orders_only: Any,
    root: Path = PROJECT_ROOT,
    at: datetime | None = None,
) -> dict[str, Any]:
    """The basket the run built (build_paper_basket), against the engine's from the
    same inputs, and the auction the batch would go to now."""
    at = at or datetime.now(timezone.utc)

    def compare() -> dict[str, Any]:
        from dv02_engine import oms

        b = _plan(oms, latest, positions, nav, cash_buffer=float(cash_buffer),
                  min_trade_dollars=float(min_trade_dollars), model=list(model), whitelist=list(symbol_whitelist),
                  last_closes=dict(last_closes), cash=None if cash is None else float(cash))  # fmt: skip
        if not b.ok:
            return {"error": f"the engine refused a basket Python built: {b.error}"}
        out = compare_basket(recon, orders_only, b)
        if len(orders_only):
            ex = compare_execution(list(orders_only["symbol"]), at)
            out["differences"] += ex["differences"]
            out["execution"] = {"python": ex["python"], "engine": ex["engine"]}
        out["engine_warnings"] = list(b.warnings)
        return out

    return _guarded("basket", root, at, compare)


def whatif(positions: Any, nav: float | None, *, root: Path = PROJECT_ROOT, at: datetime | None = None) -> dict:
    """The basket the run would build now, Python's and the engine's, from the
    positions and NAV the gate read (no cash: nothing is trimmed). Runs on every
    run, so the basket is compared every session, not only on rebalance days."""
    at = at or datetime.now(timezone.utc)

    def compare() -> dict[str, Any]:
        from dv02_engine import oms

        from src.production.generate_targets import model_universe
        from src.production.runtime import build_paper_basket as bpb

        if positions is None or not nav or nav <= 0:
            return {"error": "no positions or NAV from the gate to build a basket on"}
        cfg = bpb.default_config()
        cfg.targets_path = root / cfg.targets_path.relative_to(bpb._project_root())
        cfg.prices_path = root / cfg.prices_path.relative_to(bpb._project_root()) if cfg.prices_path else None
        latest = bpb.load_latest_targets(cfg.targets_path)
        model = model_universe()
        closes = bpb.load_last_closes(cfg.prices_path)
        kw = dict(cash_buffer=cfg.cash_buffer, min_trade_dollars=cfg.min_trade_dollars, model=list(model),
                  whitelist=list(cfg.symbol_whitelist), last_closes=closes)  # fmt: skip
        b = _plan(oms, latest, positions, nav, **kw)
        try:
            recon, orders_only = bpb.build_reconciliation(
                latest, positions, float(nav), cash_buffer=cfg.cash_buffer, min_trade_dollars=cfg.min_trade_dollars,
                symbol_whitelist=cfg.symbol_whitelist, model=model, last_closes=closes, cash=None,
            )  # fmt: skip
        except Exception as exc:  # noqa: BLE001 — Python refusing is an outcome to compare
            if b.ok:
                return {"error": f"Python refused the basket ({type(exc).__name__}: {exc}); the engine built one"}
            return {"differences": [], "python_orders": [], "engine_orders": [],
                    "both_refused": {"python": f"{type(exc).__name__}: {exc}", "engine": b.error}}  # fmt: skip
        if not b.ok:
            return {"error": f"the engine refused a basket Python built: {b.error}"}
        out = compare_basket(recon, orders_only, b)
        symbols = list(orders_only["symbol"]) if len(orders_only) else list(latest["symbol"])
        ex = compare_execution(symbols, at)
        out["differences"] += ex["differences"]
        out["execution"] = {"python": ex["python"], "engine": ex["engine"]}
        return out

    return _guarded("whatif", root, at, compare)


def drift(*, root: Path = PROJECT_ROOT, at: datetime | None = None, **inputs: Any) -> dict[str, Any]:
    """The rebalance gate, Python's decision against the engine's (compare_drift's inputs)."""
    return _guarded("drift", root, at, lambda: compare_drift(**inputs))


def orders(submission_path: Any, *, root: Path = PROJECT_ROOT, at: datetime | None = None) -> dict[str, Any]:
    """The orders a run sent (its submission log), replayed: compare_orders."""

    def compare() -> dict[str, Any]:
        with Path(submission_path).open(newline="", encoding="utf-8") as f:
            return compare_orders(list(csv.DictReader(f)))

    return _guarded("orders", root, at, compare)


def reconcile(*, root: Path = PROJECT_ROOT, at: datetime | None = None, **inputs: Any) -> dict[str, Any]:
    """The reconcile, Python's breaks and verdict against the engine's (compare_reconcile's inputs)."""
    return _guarded("reconcile", root, at, lambda: compare_reconcile(**inputs))


# ── the record ───────────────────────────────────────────────────────────


def read_record(root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    path = shadow_dir(root) / "record.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["match"] = r.get("match") == "1"
        r["differences"] = int(r.get("differences") or 0)
    return rows


def status(root: Path = PROJECT_ROOT, *, sessions: int = 15) -> dict[str, Any]:
    """The record by session: a session matches when every comparison it ran
    matched. The streak counts back from the latest session."""
    rows = read_record(root)
    by_session: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_session.setdefault(r["session"], []).append(r)
    days = []
    for session in sorted(by_session):
        runs = by_session[session]
        latest = {}
        for r in runs:
            latest[r["kind"]] = r  # the session's last comparison of each kind
        days.append({
            "session": session,
            "match": all(r["match"] for r in runs),
            "comparisons": len(runs),
            "kinds": {k: {"match": r["match"], "summary": r["summary"], "at": r["at"]} for k, r in latest.items()},
        })  # fmt: skip
    streak = 0
    for d in reversed(days):
        if not d["match"]:
            break
        streak += 1
    last_miss = next((d for d in reversed(days) if not d["match"]), None)
    return {
        "streak": streak,
        "needed": STREAK_NEEDED,
        "ready": streak >= STREAK_NEEDED,
        "sessions": list(reversed(days))[:sessions],
        "last_difference": last_miss,
        "comparisons": len(rows),
    }


def main(argv: list[str] | None = None) -> int:
    s = status()
    if not s["comparisons"]:
        print("The engine shadow has no comparisons yet: the next daily run makes the first.")
        return 0
    print(f"The C++ engine in shadow: {s['streak']} session{'s' if s['streak'] != 1 else ''} in a row matching "
          f"Python, of the {s['needed']} needed before it may take over sending.")  # fmt: skip
    for d in s["sessions"]:
        mark = "match" if d["match"] else "DIFFERS"
        kinds = ", ".join(f"{k} {'ok' if v['match'] else 'differs'}" for k, v in sorted(d["kinds"].items()))
        print(f"  {d['session']}  {mark:7}  {kinds}")
        for k, v in sorted(d["kinds"].items()):
            if not v["match"]:
                print(f"      {k}: {v['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
