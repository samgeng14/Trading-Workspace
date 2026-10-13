"""
price_basis.py — keep the price master on one price basis.

IBKR's daily bars are adjusted as of the day they are requested (TRADES bars
for splits; ADJUSTED_LAST bars for splits and dividends). After a split, a
reverse split or a large distribution IBKR rescales every older bar, but rows
already in the master keep the basis they were written on. Splicing new IBKR
bars onto them leaves a jump that never traded and a level shift at the seam:
ETHA's 1-for-3 reverse split showed up as a +220% day on 2026-02-13 (old rows
around 15, IBKR's rescaled rows around 46). Momentum, volatility and basket
sizing all read those closes.

IBKR-agnostic logic (fetchers are plain callables, so tests use fakes):

  * check_overlap        new bars vs the master's rows on the dates both have
  * merge_symbol_bars    merge one symbol's new bars; on a basis mismatch
                         replace its whole history with a fresh refetch, or
                         refuse the merge. Never splice two bases.
  * merge_into_master    the same for every symbol in the master
  * find_bad_jumps       split-ratio jumps and one-day spikes that revert:
                         bad data, not market moves
  * repair_master_basis  find symbols whose stored history is already mixed
                         (bad jumps, or disagreement with IBKR now) and
                         replace them with a fresh refetch
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]

# Largest disagreement between IBKR's close and the master's close on an
# overlapping day that still counts as the same basis (|new/old - 1|).
# Settled same-basis bars come back identical: the 11,448 settled rows in the
# repo's 52 IBKR append snapshots (data/market/snapshots) match the master
# exactly. A dividend moves the whole history only for dividend-adjusted bars
# (ADJUSTED_LAST), by about the payout, a fraction of a percent per ex-date.
# The smallest split this module looks for moves it by 50%. 0.5% sits above
# that drift and two orders of magnitude below a split, and a disagreement
# above it costs a refetch, not a failure, so erring low is safe.
BASIS_TOLERANCE = 0.005

# Split / reverse-split factors (each factor and its reciprocal); the
# smallest sets the line for a split-sized move. 3:2 is left out on purpose: a ±33-50% day
# is a real (if extreme) move for leveraged and volatility ETFs, and those
# still get the validator's >50% warning.
# Issuers pick arbitrary ratios (5-for-2, 1-for-6, 1-for-15, 1-for-40 ...),
# so every one-day ratio from x1.74 (or /1.74) outward counts as a split
# ratio (see split_factor); this list only names the closest one. Matching
# only exact factors (2, 3, 4, 5, 8, 10, 20, 25, 50) within 15% left gaps: a
# 5-for-2 split (/2.5), a 1-for-6, 1-for-12, 1-for-15, 1-for-30 or 1-for-40
# reverse split, or a 1-for-2 on a +20% day (x2.4) spliced in drew only a
# warning.
SPLIT_FACTORS: tuple[float, ...] = (2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 75, 100)

# A split day's close ratio is the split factor times that day's own move:
# ETHA's 1-for-3 arrived as x3.20 (factor 3, +6.8% on the day). 15% leaves
# room for a big day in a volatile ETF; the largest genuine moves in the
# committed master (1993-2026: TQQQ -34.5% on 2020-03-16, VIXY +43% on
# 2024-08-05) are 31% and 40% away from 1/2 and 2, so they don't match.
SPLIT_MATCH_TOLERANCE = 0.15

# A one-day spike that reverts: the close moves by at least this much in log
# terms (+50% or -33%) and the next close lands back within SPIKE_REVERT_BAND
# of the close before the spike. That is a bad bar, not a market move. No
# such pattern exists anywhere in the committed master.
SPIKE_MIN_LOG_MOVE = math.log(1.5)
SPIKE_REVERT_BAND = 0.10

# How many of the master's newest rows are left out of the overlap check when
# the overlap is long enough: the newest row may have been written from a
# provisional intraday bar (all 72 snapshot closes more than 0.5% off the
# final close, up to 3.3%, were the newest bar of a morning fetch), and
# IBKR's newer bar replaces it anyway. A basis change shows on every day.
PROVISIONAL_TAIL_ROWS = 1

# A refetched history replaces the stored one only if it reaches back far
# enough: to the stored history's first day (within REFETCH_START_SLACK_DAYS),
# or at least MIN_REFETCH_HISTORY_DAYS before its own last day (the window the
# signals read; validators.SIGNAL_WINDOW_DAYS). A shorter series is refused:
# taking it would silently drop history the targets need. It must also reach
# the stored history's end (within REFETCH_END_SLACK_DAYS) and overlap the
# bars just fetched, or replacing would leave a hole.
MIN_REFETCH_HISTORY_DAYS = 400
REFETCH_START_SLACK_DAYS = 31
REFETCH_END_SLACK_DAYS = 10

# (symbol, duration) -> bars in COLUMNS
Fetcher = Callable[[str, str], pd.DataFrame]
# symbol -> the symbol's full, consistent history in COLUMNS
Refetcher = Callable[[str], pd.DataFrame]


# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize_bars(df: pd.DataFrame | None, symbol: str | None = None) -> pd.DataFrame:
    """Long-format bars with normalized dates, upper-case symbols, sorted, one row per date."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLUMNS)
    out = df.copy()
    out.columns = [str(c).lower().strip() for c in out.columns]
    if symbol is not None and "symbol" not in out.columns:
        out["symbol"] = symbol
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date"])
    out = out.drop_duplicates(subset=["symbol", "date"], keep="last")
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)[COLUMNS]


def _combine(base: pd.DataFrame, newer: pd.DataFrame) -> pd.DataFrame:
    """Union of two same-basis frames; ``newer`` wins on shared dates."""
    frames = [f for f in (base, newer) if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["symbol", "date"], keep="last")
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)[COLUMNS]


def split_factor(ratio: float, tolerance: float = SPLIT_MATCH_TOLERANCE) -> float | None:
    """The split factor a one-day close ratio matches (3.0 for x3, 0.5 for /2), or None.

    A ratio matches when it is at least as far from 1 as the smallest split
    (2:1) allowing ``tolerance`` for the day's own move: x1.74 or more, /1.74
    or less. Issuers pick arbitrary ratios, so there is no gap above that
    line; the result names the closest factor in SPLIT_FACTORS (a ratio
    beyond the largest one is named by its own rounded value).
    """
    if not (isinstance(ratio, (int, float)) and math.isfinite(ratio) and ratio > 0):
        return None
    lr = math.log(ratio)
    if abs(lr) < math.log(min(SPLIT_FACTORS)) - math.log1p(tolerance):
        return None
    if abs(lr) > math.log(max(SPLIT_FACTORS)) + math.log1p(tolerance):
        return float(round(ratio)) if ratio > 1 else 1.0 / round(1.0 / ratio)
    candidates = [float(n) if lr > 0 else 1.0 / n for n in SPLIT_FACTORS]
    return min(candidates, key=lambda f: abs(lr - math.log(f)))


def describe_factor(f: float) -> str:
    if f >= 1:
        fr = Fraction(f).limit_denominator(10)            # 3 -> 3/1, 2.5 -> 5/2
        return f"x{f:g} (a {fr.denominator}-for-{fr.numerator} reverse split)"
    n = 1 / f
    fr = Fraction(n).limit_denominator(10)
    return f"/{n:g} (a {fr.numerator}-for-{fr.denominator} split)"


# ── Bad jumps (split-ratio moves, reverting spikes) ──────────────────────────

@dataclass(frozen=True)
class BadJump:
    symbol: str
    date: pd.Timestamp
    prev_close: float
    close: float
    kind: str                 # "split" | "spike"
    factor: float | None = None
    next_close: float | None = None

    @property
    def ratio(self) -> float:
        return self.close / self.prev_close

    def describe(self) -> str:
        d = self.date.date()
        move = f"{self.prev_close:g} -> {self.close:g} ({(self.ratio - 1) * 100:+.0f}%)"
        if self.kind == "split":
            return (
                f"{self.symbol} {d}: close {move} matches a split ratio, "
                f"{describe_factor(self.factor or 1.0)}: the price history mixes price bases"
            )
        return (
            f"{self.symbol} {d}: one-day spike {move} that reverts the next day "
            f"(to {self.next_close:g}): a bad bar"
        )


def find_bad_jumps(
    df: pd.DataFrame,
    *,
    since: pd.Timestamp | None = None,
    split_tolerance: float = SPLIT_MATCH_TOLERANCE,
    spike_min_log_move: float = SPIKE_MIN_LOG_MOVE,
    spike_revert_band: float = SPIKE_REVERT_BAND,
) -> list[BadJump]:
    """Split-ratio jumps and reverting one-day spikes in long-format prices.

    Needs ``date``, ``symbol`` and ``close``. Rows with a missing or
    non-positive close are skipped (the validator reports those separately).
    ``since`` keeps only jumps dated on or after it.
    """
    if df is None or len(df) == 0:
        return []
    d = df[["date", "symbol", "close"]].copy()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d["symbol"] = d["symbol"].astype(str).str.upper().str.strip()
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d[d["close"].notna() & (d["close"] > 0)]
    d = d.drop_duplicates(subset=["symbol", "date"], keep="last")
    d = d.sort_values(["symbol", "date"]).reset_index(drop=True)
    if d.empty:
        return []

    g = d.groupby("symbol")["close"]
    d["prev"] = g.shift(1)
    d["next"] = g.shift(-1)
    d["lr"] = np.log(d["close"] / d["prev"])
    reverted = np.log(d["next"] / d["prev"]).abs() <= math.log1p(spike_revert_band)
    d["spike"] = (d["lr"].abs() >= spike_min_log_move) & reverted
    # The day a spike reverts is part of the same bad bar, not a second jump.
    d["after_spike"] = d.groupby("symbol")["spike"].shift(1, fill_value=False).astype(bool)

    candidates = d[d["prev"].notna() & (d["spike"] | (d["lr"].abs() >= math.log(2) - math.log1p(split_tolerance)))]
    out: list[BadJump] = []
    for row in candidates.itertuples(index=False):
        if since is not None and row.date < since:
            continue
        if row.spike:
            out.append(BadJump(row.symbol, row.date, float(row.prev), float(row.close), "spike",
                               factor=split_factor(row.close / row.prev, split_tolerance),
                               next_close=float(row.next)))
            continue
        if row.after_spike:
            continue
        f = split_factor(row.close / row.prev, split_tolerance)
        if f is not None:
            out.append(BadJump(row.symbol, row.date, float(row.prev), float(row.close), "split", factor=f))
    return out


# ── Overlap check ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OverlapCheck:
    days: int
    median_ratio: float | None = None
    max_deviation: float | None = None
    consistent: bool = False
    tolerance: float = BASIS_TOLERANCE

    def describe(self) -> str:
        if self.days == 0:
            return "no overlapping dates between IBKR's bars and the master, so the price basis can't be checked"
        word = "agree" if self.consistent else "disagree"
        return (
            f"IBKR's bars {word} with the master on {self.days} overlapping days "
            f"(IBKR/master close: median x{self.median_ratio:.4f}, worst day "
            f"{self.max_deviation * 100:.2f}% off; tolerance {self.tolerance * 100:.1f}%)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overlap_days": self.days,
            "median_ratio": self.median_ratio,
            "max_deviation": self.max_deviation,
            "consistent": self.consistent,
        }


def check_overlap(
    existing: pd.DataFrame,
    new: pd.DataFrame,
    tolerance: float = BASIS_TOLERANCE,
    *,
    skip_provisional_tail: bool = True,
) -> OverlapCheck:
    """Compare closes on the dates both frames have (one symbol each).

    Consistent when every overlapping day agrees within ``tolerance``. With
    ``skip_provisional_tail`` the existing frame's newest row is left out
    when other overlapping days remain (see PROVISIONAL_TAIL_ROWS).
    """
    e = existing.dropna(subset=["close"])
    e = e[e["close"] > 0].drop_duplicates(subset=["date"], keep="last").set_index("date")["close"]
    n = new.dropna(subset=["close"])
    n = n[n["close"] > 0].drop_duplicates(subset=["date"], keep="last").set_index("date")["close"]
    common = e.index.intersection(n.index).sort_values()
    if skip_provisional_tail and len(e) and len(common) > PROVISIONAL_TAIL_ROWS:
        tail = set(e.sort_index().index[-PROVISIONAL_TAIL_ROWS:])
        common = common[[d not in tail for d in common]]
    if len(common) == 0:
        return OverlapCheck(0, tolerance=tolerance)
    ratios = (n.loc[common] / e.loc[common]).astype(float)
    max_dev = float((ratios - 1.0).abs().max())
    return OverlapCheck(
        days=int(len(common)),
        median_ratio=float(ratios.median()),
        max_deviation=max_dev,
        consistent=bool(max_dev <= tolerance),
        tolerance=tolerance,
    )


# ── Merging ──────────────────────────────────────────────────────────────────

@dataclass
class SymbolMerge:
    symbol: str
    # "merged"     new bars agree with the master; spliced in (IBKR wins on shared dates)
    # "added"      symbol had no rows yet
    # "unchanged"  nothing new for this symbol (or the repair found nothing wrong)
    # "unchecked"  repair only: no recent IBKR bars to compare against; left as is
    # "refetched"  basis mismatch; whole history replaced with a fresh IBKR history
    # "refused"    basis mismatch and no consistent history to replace it with;
    #              the master keeps this symbol's old rows and gets none of the new ones
    status: str
    rows: pd.DataFrame
    overlap: OverlapCheck | None = None
    reason: str = ""
    rows_dropped: int = 0       # old rows older than the refetched history's first date
    first_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.overlap is not None:
            out.update(self.overlap.to_dict())
        if self.reason:
            out["reason"] = self.reason
        if self.status == "refetched":
            out["rows_dropped"] = self.rows_dropped
            out["first_date"] = self.first_date
        return out


def replace_history(
    symbol: str,
    existing: pd.DataFrame,
    new: pd.DataFrame,
    refetch: Refetcher | None,
    reason: str,
    tolerance: float = BASIS_TOLERANCE,
    overlap: OverlapCheck | None = None,
) -> SymbolMerge:
    """Replace ``symbol``'s history with ``refetch(symbol)``, or refuse.

    The fresh history must be non-empty, free of split-ratio jumps and
    reverting spikes, long enough (see MIN_REFETCH_HISTORY_DAYS), reach the
    stored history's end, and overlap and agree with ``new`` (bars fetched
    moments ago). Otherwise the symbol's old rows are kept as they are and
    the new bars are not merged.
    """
    def refuse(why: str) -> SymbolMerge:
        return SymbolMerge(symbol, "refused", existing, overlap, f"{reason}; {why}")

    if refetch is None:
        return refuse("no way to refetch its history")
    try:
        full = normalize_bars(refetch(symbol), symbol)
    except Exception as exc:  # noqa: BLE001 — any fetch failure means refuse
        return refuse(f"refetching its full history failed: {type(exc).__name__}: {exc}")
    full = full[full["symbol"] == symbol]
    if full.empty:
        return refuse("refetching its full history returned no bars")
    jumps = find_bad_jumps(full)
    if jumps:
        return refuse(
            "the refetched history has bad jumps of its own (IBKR may not have "
            f"adjusted it yet): {jumps[0].describe()}"
        )
    first, last = full["date"].min(), full["date"].max()
    if len(existing):
        need_from = max(existing["date"].min(), last - pd.Timedelta(days=MIN_REFETCH_HISTORY_DAYS))
        if first > need_from + pd.Timedelta(days=REFETCH_START_SLACK_DAYS):
            return refuse(
                f"IBKR returned only {len(full)} bars ({first.date()} to {last.date()}), shorter than "
                f"the stored history (from {existing['date'].min().date()}) and than the "
                f"{MIN_REFETCH_HISTORY_DAYS} days the signals read; replacing would drop history"
            )
        if last < existing["date"].max() - pd.Timedelta(days=REFETCH_END_SLACK_DAYS):
            return refuse(
                f"the refetched history ends {last.date()}, before the stored rows "
                f"(to {existing['date'].max().date()}); replacing would drop recent history"
            )
    new = new[new["symbol"] == symbol] if len(new) else new
    if len(new):
        # The newest bar may be today's, still trading: it can move between
        # the two requests, so it is left out like the master's newest row.
        agree = check_overlap(full, new, tolerance)
        if agree.days == 0:
            return refuse(
                f"the refetched history ({first.date()} to {last.date()}) doesn't overlap the "
                "bars just fetched, so the two can't be checked against each other"
            )
        if not agree.consistent:
            return refuse(f"the refetched history disagrees with the bars just fetched: {agree.describe()}")
        full = _combine(full, new[new["date"] > full["date"].max()])
    first = full["date"].min()
    dropped = int((existing["date"] < first).sum()) if len(existing) else 0
    return SymbolMerge(
        symbol, "refetched", full, overlap, reason,
        rows_dropped=dropped, first_date=str(first.date()),
    )


def merge_symbol_bars(
    symbol: str,
    existing: pd.DataFrame,
    new: pd.DataFrame,
    refetch: Refetcher | None,
    tolerance: float = BASIS_TOLERANCE,
) -> SymbolMerge:
    """Merge one symbol's new IBKR bars into its master rows, checking the basis first."""
    existing = normalize_bars(existing, symbol)
    new = normalize_bars(new, symbol)
    if new.empty:
        return SymbolMerge(symbol, "unchanged", existing)
    if existing.empty:
        return SymbolMerge(symbol, "added", new)
    ov = check_overlap(existing, new, tolerance)
    if not ov.consistent:
        return replace_history(symbol, existing, new, refetch, ov.describe(), tolerance, ov)
    merged = _combine(existing, new)
    # The overlap agrees, but the new bars themselves may carry a jump: on a
    # split's first day IBKR can send the post-split bar before rescaling the
    # older ones. Look at the seam and everything after it.
    prior = existing[existing["date"] < new["date"].min()]["date"]
    seam = prior.max() if len(prior) else new["date"].min()
    jumps = find_bad_jumps(merged, since=seam)
    if jumps:
        reason = f"the new bars bring a bad jump into the master: {jumps[0].describe()}"
        return replace_history(symbol, existing, new, refetch, reason, tolerance, ov)
    return SymbolMerge(symbol, "merged", merged, ov)


@dataclass
class MergeReport:
    master: pd.DataFrame
    results: dict[str, SymbolMerge] = field(default_factory=dict)

    def symbols(self, status: str) -> list[str]:
        return sorted(s for s, r in self.results.items() if r.status == status)

    @property
    def refetched(self) -> list[str]:
        return self.symbols("refetched")

    @property
    def refused(self) -> list[dict[str, str]]:
        return [{"symbol": s, "reason": self.results[s].reason} for s in self.symbols("refused")]

    @property
    def changed(self) -> bool:
        return any(r.status in ("merged", "added", "refetched") for r in self.results.values())

    def basis(self) -> dict[str, dict[str, Any]]:
        return {s: r.to_dict() for s, r in sorted(self.results.items())}


def _assemble(master: pd.DataFrame, results: dict[str, SymbolMerge]) -> pd.DataFrame:
    untouched = master[~master["symbol"].isin(results.keys())]
    frames = [untouched] + [r.rows for r in results.values() if len(r.rows)]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)[COLUMNS]


def merge_into_master(
    master: pd.DataFrame,
    fetched: pd.DataFrame,
    refetch: Refetcher | None,
    tolerance: float = BASIS_TOLERANCE,
) -> MergeReport:
    """Merge freshly fetched bars (any symbols) into the master, one basis check per symbol."""
    master = normalize_bars(master)
    fetched = normalize_bars(fetched)
    results: dict[str, SymbolMerge] = {}
    for sym in sorted(set(fetched["symbol"])):
        results[sym] = merge_symbol_bars(
            sym, master[master["symbol"] == sym], fetched[fetched["symbol"] == sym], refetch, tolerance,
        )
    return MergeReport(_assemble(master, results), results)


def repair_master_basis(
    master: pd.DataFrame,
    refetch: Refetcher,
    *,
    recent: Callable[[str], pd.DataFrame] | None = None,
    symbols: Iterable[str] | None = None,
    tolerance: float = BASIS_TOLERANCE,
) -> MergeReport:
    """Find symbols whose stored history is mixed and replace it with a fresh refetch.

    A symbol is flagged when its rows contain a split-ratio jump or a
    reverting spike anywhere, or (with ``recent``, which returns IBKR's bars
    for a recent window) when IBKR's bars disagree with the stored rows on
    the overlap. Flagged symbols get ``refetch(symbol)`` or are refused;
    everything else is left untouched. A failure of ``recent`` alone is
    reported with status "unchecked" and leaves the symbol untouched.
    """
    master = normalize_bars(master)
    wanted = sorted({s.upper().strip() for s in symbols} if symbols else set(master["symbol"]))
    results: dict[str, SymbolMerge] = {}
    for sym in wanted:
        existing = master[master["symbol"] == sym]
        if existing.empty:
            results[sym] = SymbolMerge(sym, "unchanged", existing, reason="not in the master")
            continue
        reasons = [j.describe() for j in find_bad_jumps(existing)]
        ov: OverlapCheck | None = None
        unchecked = ""
        if recent is not None:
            try:
                bars = normalize_bars(recent(sym), sym)
            except Exception as exc:  # noqa: BLE001
                bars = pd.DataFrame(columns=COLUMNS)
                unchecked = f"couldn't fetch recent bars to compare: {type(exc).__name__}: {exc}"
            if len(bars):
                ov = check_overlap(existing, bars, tolerance)
                if not ov.consistent:
                    reasons.append(ov.describe())
            elif not unchecked:
                unchecked = "IBKR returned no recent bars to compare"
        if reasons:
            results[sym] = replace_history(
                sym, existing, pd.DataFrame(columns=COLUMNS), refetch, "; ".join(reasons), tolerance, ov,
            )
        elif unchecked:
            results[sym] = SymbolMerge(sym, "unchecked", existing, reason=unchecked)
        else:
            results[sym] = SymbolMerge(sym, "unchanged", existing, ov)
    return MergeReport(_assemble(master, results), results)
