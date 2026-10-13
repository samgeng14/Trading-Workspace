"""
leverage.py — The no-leverage check on the whole book, before orders go out.

The fund is long/short with no leverage: gross = (long + |short|) / NAV <= 100%
with futures at notional and leveraged or inverse ETFs at their economic
exposure (EXPOSURE below), and cash >= 0 after the orders (no margin loan). The
rules live in the C++ engine (engine/src/risk.cpp, ``dv02_engine.risk``); this
module builds its input from the run's own files:

  * every position in ``{profile}_positions_snapshot.csv`` (build_paper_basket),
    priced at IBKR's mark, else the reconciliation close, else the last close
    in the master price file, else left unpriced, which blocks
  * NAV and cash from the latest ``{profile}_account_summary_*.csv``
  * the orders in exactly the file submit_paper_orders sends

The exposure limits in risk_checks.py look only at target weights; this counts
holdings outside the model too. The daily runner calls both from
``step_submit_orders``, and either one blocks.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.runtime.risk_checks import PreTradeRiskResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFIX = "no leverage"

# Economic exposure per dollar of market value, for shares that are not one
# dollar of exposure per dollar held. The engine counts a position's market
# value times this toward gross and net; cash still moves at market value.
# A leveraged ETF goes in at its multiple (UPRO 3.0, SSO 2.0); an inverse ETF
# goes in as a negative factor (SH -1.0, SQQQ -3.0), so holding it long counts
# as a short. Shares not listed are 1.0. Futures and options are measured by
# their multiplier instead and always stay at 1.0. The API's no-leverage check
# (services/api/dv02_api/mandate.py) reads this same table.
EXPOSURE: dict[str, float] = {"TQQQ": 3.0}
SHARES = ("", "STK", "ETF")


def exposure_of(symbol: str, sec_type: str = "STK") -> float:
    """``symbol``'s exposure per dollar held: EXPOSURE for shares, 1.0 for anything else."""
    if (sec_type or "") not in SHARES:
        return 1.0
    return EXPOSURE.get(str(symbol).upper().strip(), 1.0)


@dataclass
class LeverageResult(PreTradeRiskResult):
    def report(self) -> str:
        head = f"No-leverage check: {'PASS' if self.ok else 'BLOCK'}"
        before, after = self.details.get("before"), self.details.get("after")
        if before and after:
            head += (
                f" (gross {before['gross']:.1%} -> {after['gross']:.1%} of NAV, "
                f"cash {_money(before['cash'])} -> {_money(after['cash'])} "
                f"after {self.details.get('orders', 0)} orders)"
            )
        out = [head]
        out += [f"  ✗ {r}" for r in self.blocking_reasons]
        out += [f"  ⚠ {w}" for w in self.warnings]
        return "\n".join(out)


def _money(v: float) -> str:
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def _blocked(reason: str, **details: Any) -> LeverageResult:
    return LeverageResult(ok=False, blocking_reasons=[f"{PREFIX}: {reason}"], details=details)


def _positive(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def orders_file() -> Path:
    """The file submit_paper_orders reads: whatever its default_config says."""
    from src.production.runtime.submit_paper_orders import default_config

    return default_config().orders_path


def _read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def _account(folder: Path, profile: str) -> tuple[Path | None, float, float]:
    """NAV and cash from the latest account summary; NaN where a tag is missing."""
    files = sorted(folder.glob(f"{profile}_account_summary_*.csv"))
    if not files:
        return None, math.nan, math.nan
    summary = pd.read_csv(files[-1])
    tag_col = "tag" if "tag" in summary.columns else summary.columns[0]
    val_col = "value" if "value" in summary.columns else summary.columns[1]
    tags = dict(zip(summary[tag_col].astype(str), summary[val_col]))

    def num(tag: str) -> float:
        try:
            return float(str(tags[tag]).replace(",", ""))
        except (KeyError, ValueError):
            return math.nan

    return files[-1], num("NetLiquidation"), num("TotalCashValue")


def _closes(root: Path, profile: str) -> tuple[dict[str, float], dict[str, float]]:
    """Fallback prices: the reconciliation's closes (the targets'), and the master's last close."""
    recon: dict[str, float] = {}
    base = root / "data" / "broker" / "reconciliations" / f"{profile}_reconciliation.parquet"
    for path in (base, base.with_suffix(".csv")):
        if path.exists():
            df = _read_table(path)
            if {"symbol", "close"} <= set(df.columns):
                recon = {str(s).upper(): float(px) for s, px in zip(df["symbol"], df["close"]) if _positive(px)}
            break
    master: dict[str, float] = {}
    path = root / "data" / "market" / "cleaned" / "prices" / "etf_prices_master.parquet"
    if path.exists():
        px = pd.read_parquet(path, columns=["date", "symbol", "close"]).dropna(subset=["close"])
        last = px.sort_values("date").groupby("symbol").tail(1)
        master = {str(s).upper(): float(c) for s, c in zip(last["symbol"], last["close"]) if _positive(c)}
    return recon, master


def check_leverage(
    profile: str = "paper",
    *,
    root: Path | None = None,
    orders_path: Path | None = None,
) -> LeverageResult:
    """Whether the book after the run's orders stays unlevered. Anything that can't be
    measured (no engine, no snapshot, no NAV, an unpriced holding) blocks."""
    try:
        from dv02_engine import risk
    except ImportError:
        return _blocked(
            f"the C++ engine (dv02_engine) is not installed for {sys.executable}, so leverage can't be "
            "checked. Run bin/tradeit setup (it offers to install it into the daily runner's Python too)"
        )

    root = root or PROJECT_ROOT
    orders_path = orders_path or orders_file()
    positions_path = root / "data" / "broker" / "positions" / f"{profile}_positions_snapshot.csv"
    account_path, nav, cash = _account(root / "data" / "broker" / "account", profile)
    files = {"positions": str(positions_path), "account": str(account_path), "orders": str(orders_path)}
    if not positions_path.exists():
        return _blocked(f"positions snapshot not found: {positions_path}", files=files)
    if account_path is None:
        return _blocked(f"no {profile} account summary in {root / 'data' / 'broker' / 'account'}", files=files)
    if not math.isfinite(nav):
        return _blocked(f"no NetLiquidation in {account_path}", files=files)
    if not orders_path.exists():
        return _blocked(f"orders file not found: {orders_path}", files=files)

    recon, master = _closes(root, profile)
    sources: dict[str, str] = {}

    def price(symbol: str, mark: Any = None) -> float:
        candidates = {"ibkr": _positive(mark), "reconciliation": recon.get(symbol), "master": master.get(symbol)}
        for source, px in candidates.items():
            if px:
                sources[symbol] = source
                return px
        sources[symbol] = "none"
        return 0.0  # the engine blocks it as unpriced

    snapshot = pd.read_csv(positions_path)
    positions = []
    for row in snapshot.to_dict("records"):
        qty = float(row.get("current_shares") or 0.0)
        if not qty:
            continue
        symbol = str(row["symbol"]).upper()
        sec_type = row.get("sec_type") if isinstance(row.get("sec_type"), str) else ""
        # shares are one unit each; a future or option without its multiplier can't be measured
        multiplier = _positive(row.get("multiplier")) or (1.0 if sec_type in SHARES else math.nan)
        positions.append(
            risk.Position(
                symbol,
                qty,
                price(symbol, row.get("market_price")),
                multiplier,
                risk.asset_class_from_ibkr(sec_type),
                exposure_of(symbol, sec_type),
            )
        )

    # what submit_paper_orders.load_orders sends: side with |delta_shares| as a whole number
    planned = _read_table(orders_path)
    planned = planned.loc[planned["side"].isin(["BUY", "SELL"])]
    orders = []
    for row in planned.to_dict("records"):
        qty = abs(int(row["delta_shares"]))
        if not qty:
            continue
        symbol = str(row["symbol"]).upper()
        orders.append(
            risk.Order(
                symbol,
                float(qty if row["side"] == "BUY" else -qty),
                _positive(row.get("close")) or price(symbol),
                1.0,
                risk.AssetClass.ETF,
                float(row.get("estimated_commission", 0.0)),
                exposure_of(symbol),
            )
        )

    result = risk.check(risk.Book(nav, cash, positions), orders, risk.Policy())
    details = risk.result_dict(result)
    details.update(orders=len(orders), files=files, price_sources=sources)
    return LeverageResult(
        ok=result.ok,
        blocking_reasons=[f"{PREFIX}: {b.message}" for b in result.breaches if b.blocking],
        warnings=[f"{PREFIX}: {b.message}" for b in result.breaches if not b.blocking],
        details=details,
    )
