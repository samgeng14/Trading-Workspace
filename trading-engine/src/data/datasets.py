"""
datasets.py — Every dataset the fund keeps: how fresh it is, how fresh it should be
by now, and how to fill it when it falls behind (docs/PLAN.md §11, "Pull whatever
data is missing").

    python -m src.data.datasets              # bin/tradeit data: each one's watermark and state
    python -m src.data.datasets --catchup    # bin/tradeit catchup: fill the ones behind, record what was done

A dataset's watermark is the latest session it holds, its rows and when it was
last written. What it should hold by now follows the NYSE calendar and the
schedule of the job that fills it:
  * the daily run (15:35 ET) appends bars through the session before;
  * the API records the close 15 minutes after it;
  * the curve archive records the day at 17:20 ET;
  * Flex publishes a session's statement the next morning.
Its catch-up is the idempotent job that fills it, or none where a missed day can't
be had again (the account's value at a close, a day's executions).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.common import clock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NY = clock.NYSE_TZ

RUN_AT = time(15, 35)  # the daily run (deploy/systemd/dv02-runner.timer)
RUN_DONE = time(16, 0)  # by when it has written its bars
DOCTOR_DONE = time(15, 25)  # dv02-doctor.timer, 15:15
SHADOW_DONE = time(17, 20)  # dv02-shadow.timer, 17:05, which fetches Cboe's VX files
CLOSE_RECORDED = time(16, 30)  # the API records the close 15 minutes after it
CAPTURE_DONE = time(16, 45)  # dv02-capture.timer, 16:30
ARCHIVE_DONE = time(17, 45)  # dv02-archive.timer, 17:20
STATEMENT_DUE = time(12, 0)  # Flex has a session's NAV by about noon the next day
# catch-up waits while the run, the capture and the archive write the same files
BUSY = (time(15, 10), time(17, 45))


@dataclass(frozen=True)
class Watermark:
    through: date | None  # the latest session it holds
    rows: int | None
    written: datetime | None  # when it was last written, UTC


@dataclass(frozen=True)
class Dataset:
    name: str
    title: str
    source: str
    schedule: str
    expected: Callable[[datetime], date | None]
    watermark: Callable[[Path, str], Watermark | None]  # None: it doesn't exist here
    catchup: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    note: str = ""  # why there is no catch-up
    off_because: Callable[[], str | None] | None = None  # why it isn't kept on this machine


@dataclass(frozen=True)
class Status:
    dataset: Dataset
    watermark: Watermark | None
    expected: date | None
    state: str  # ok, behind, missing, off
    detail: str

    def as_dict(self) -> dict[str, Any]:
        w = self.watermark
        return {
            "name": self.dataset.name,
            "title": self.dataset.title,
            "source": self.dataset.source,
            "schedule": self.dataset.schedule,
            "through": w.through.isoformat() if w and w.through else None,
            "expected": self.expected.isoformat() if self.expected else None,
            "state": self.state,
            "rows": w.rows if w else None,
            "written": w.written.isoformat(timespec="seconds") if w and w.written else None,
            "detail": self.detail,
            "can_catch_up": self.dataset.catchup is not None,
        }


# ── what should exist by now ──────────────────────────────────────────────


def latest_session_at(now: datetime, at: time) -> date | None:
    """The latest NYSE session whose ``at`` (New York time) has passed by ``now``."""
    local = now.astimezone(NY)
    day = local.date()
    for _ in range(15):
        if clock.is_us_business_day(day) and local >= datetime.combine(day, at, tzinfo=NY):
            return day
        day -= timedelta(days=1)
    return None


def after_run(now: datetime) -> date | None:
    """What the daily run has appended by ``now``: bars through the last session
    completed when it started (the session before, or the day itself after an
    early close)."""
    day = latest_session_at(now, RUN_DONE)
    return clock.last_completed_session(datetime.combine(day, RUN_AT, tzinfo=NY)) if day else None


def statement_due(now: datetime) -> date | None:
    """The latest session whose Flex statement is due: a session's arrives by
    noon the next day."""
    local = now.astimezone(NY)
    day = local.date() - timedelta(days=1 if local.time() >= STATEMENT_DUE else 2)
    for _ in range(15):
        if clock.is_us_business_day(day):
            return day
        day -= timedelta(days=1)
    return None


def cboe_due(now: datetime) -> date | None:
    """The latest session whose VX settlements the evening fetch has had: Cboe
    publishes a session's overnight, so the fetch at 17:05 ET holds the session
    before that day's."""
    day = latest_session_at(now, SHADOW_DONE)
    return clock.previous_business_day(day) if day else None


def sessions_between(after: date | None, through: date, limit: int = 10) -> list[date]:
    """The sessions after ``after`` up to ``through``, the most recent ``limit``."""
    out, day = [], through
    while (after is None or day > after) and len(out) < limit:
        if clock.is_us_business_day(day):
            out.append(day)
        day -= timedelta(days=1)
    return sorted(out)


# ── watermarks ───────────────────────────────────────────────────────────


def _written(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _parquet(relative: str, column: str) -> Callable[[Path, str], Watermark | None]:
    def read(root: Path, profile: str) -> Watermark | None:  # noqa: ARG001
        path = root / relative
        if not path.exists():
            return None
        dates = pd.to_datetime(pd.read_parquet(path, columns=[column])[column])
        return Watermark(dates.max().date() if len(dates) else None, len(dates), _written(path))

    return read


def _nav_csv(name: str) -> Callable[[Path, str], Watermark | None]:
    def read(root: Path, profile: str) -> Watermark | None:
        path = root / "artifacts" / "api" / name.format(profile=profile)
        if not path.exists():
            return None
        with path.open(newline="", encoding="utf-8") as f:
            days = [date.fromisoformat(r["date"]) for r in csv.DictReader(f) if r.get("date")]
        return Watermark(max(days) if days else None, len(days), _written(path))

    return read


def _lake(dataset: str) -> Callable[[Path, str], Watermark | None]:
    def read(root: Path, profile: str) -> Watermark | None:  # noqa: ARG001
        import pyarrow.parquet as pq

        folder = root / "data" / "lake" / dataset
        files = sorted(folder.glob("date=*/*.parquet"))
        if not files:
            return None
        days = [date.fromisoformat(f.parent.name.removeprefix("date=")) for f in files]
        rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
        return Watermark(max(days), rows, max(_written(f) for f in files))

    return read


def _doctor(root: Path, profile: str) -> Watermark | None:
    files = sorted((root / "artifacts" / "doctor").glob(f"doctor_{profile}_*.json"))
    if not files:
        return None
    days = [date.fromisoformat(f.stem.rsplit("_", 1)[-1]) for f in files]
    return Watermark(max(days), len(files), max(_written(f) for f in files))


def _store(root: Path, profile: str) -> Watermark | None:  # noqa: ARG001
    from src.storage.db import connect, load_config

    with connect(load_config()) as conn:
        through, rows = conn.execute(
            "SELECT max(nav_date), count(*) FROM nav WHERE profile = %s AND source = 'ibkr_close'", (profile,)
        ).fetchone()
        written = conn.execute("SELECT max(inserted_at) FROM fills WHERE profile = %s", (profile,)).fetchone()[0]
    return Watermark(through, int(rows), written)


def _cboe_vx(root: Path, profile: str) -> Watermark | None:  # noqa: ARG001
    from src.data.ingest import cboe_vx

    paths = cboe_vx.contract_paths(root)
    if not paths:
        return None
    cover = cboe_vx.coverage(root)
    through = cover["settlements_through"]
    return Watermark(date.fromisoformat(through) if through else None, cover["rows"],
                     max(_written(p) for p in paths))  # fmt: skip


def _store_off() -> str | None:
    from src.storage.db import load_config

    cfg = load_config()
    return None if cfg.enabled else f"the store is off here: {cfg.off_because}"


# ── catch-ups ────────────────────────────────────────────────────────────


def _bars(module: str, function: str, lookback: str | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def fill(ctx: dict[str, Any]) -> dict[str, Any]:
        import importlib

        fn = getattr(importlib.import_module(module), function)
        kwargs = {"host": ctx["host"], "port": ctx["port"], "client_id": ctx["client_id"]}
        if lookback:
            kwargs["lookback"] = lookback
        return fn(**kwargs)

    return fill


def _curves(ctx: dict[str, Any]) -> dict[str, Any]:
    from src.data.ingest import futures_curves

    return futures_curves.archive(sessions=ctx["missing"].get("futures_curves") or None)


def _cboe_now(ctx: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    from src.data.ingest import cboe_vx

    out = cboe_vx.fetch()
    return {"ok": True, "detail": f"settlements through {out['settlements_through']}, VIX through {out['vix_through']}",
            **out}  # fmt: skip


def _doctor_now(ctx: dict[str, Any]) -> dict[str, Any]:
    from src.broker.ibkr import doctor

    code = doctor.main(["--save", "--profile", ctx["profile"]])
    return {"ok": code == 0, "detail": "the doctor ran and kept today's report"}


# ── the registry ─────────────────────────────────────────────────────────

PRICES = "data/market/cleaned/prices"
DATASETS: tuple[Dataset, ...] = (
    Dataset("etf_prices", "ETF daily bars", "IBKR daily bars (trades), the model's ETFs",
            "each session at the 15:35 ET run, through the session before",
            after_run, _parquet(f"{PRICES}/etf_prices_master.parquet", "date"),
            _bars("src.production.pipeline.append_ibkr_daily", "append_ibkr_daily")),
    Dataset("total_return", "Dividend-adjusted closes", "IBKR adjusted bars: the signals' total-return prices",
            "each session after the 15:35 ET run",
            after_run, _parquet(f"{PRICES}/etf_total_return_closes.parquet", "date"),
            _bars("src.production.pipeline.append_total_return", "append_total_return")),
    Dataset("targets", "Model targets", "built from the bars by the daily run",
            "each session at the 15:35 ET run",
            after_run, _parquet("data/market/cleaned/targets/etf_targets_monthly.parquet", "rebalance_date"),
            note="rebuilt by the daily run; bin/tradeit refresh rebuilds it by hand"),
    Dataset("futures_continuous", "Futures, continuous", "IBKR continuous futures, the tracked ten",
            "each session at the 15:35 ET run, through the session before",
            after_run, _parquet(f"{PRICES}/futures_prices_master.parquet", "date"),
            _bars("src.production.pipeline.append_futures_daily", "append_futures_daily", "30 D")),
    Dataset("indices", "Indices", "IBKR index bars (VIX)",
            "each session at the 15:35 ET run, through the session before",
            after_run, _parquet(f"{PRICES}/indices_prices_master.parquet", "date"),
            _bars("src.production.pipeline.append_indices_daily", "append_indices_daily", "30 D")),
    Dataset("futures_curves", "Futures curves", "IBKR: every listed contract of 23 roots, into the lake",
            "each session at 17:20 ET",
            lambda now: latest_session_at(now, ARCHIVE_DONE), _lake("futures_curves"), _curves),
    Dataset("vx_futures", "VIX futures and the VIX", "Cboe: every listed VX contract's settlements, and the VIX close",
            "each evening at 17:05 ET with the shadow books, through the session before (Cboe publishes overnight)",
            cboe_due, _cboe_vx, _cboe_now),
    Dataset("close_nav", "NAV at the close", "the API: IBKR's net liquidation 15 minutes after the close",
            "each session, from 16:15 ET",
            lambda now: latest_session_at(now, CLOSE_RECORDED), _nav_csv("nav_{profile}.csv"),
            note="the account's value at a close can't be read later; the next day's statement replaces it"),
    Dataset("flex_nav", "NAV statements", "IBKR Flex: the official daily NAV",
            "each session's by noon the next day",
            statement_due, _nav_csv("flex_nav_{profile}.csv"),
            note="the API refreshes it every 6 hours; restarting the API refreshes it now"),
    Dataset("doctor", "Doctor reports", "the IBKR doctor, one report kept per session",
            "each session at 15:15 ET",
            lambda now: latest_session_at(now, DOCTOR_DONE), _doctor, _doctor_now),
    Dataset("store_close", "Store: fills and closing NAV", "src.storage.capture: the day's executions and NAV",
            "each session at 16:30 ET",
            lambda now: latest_session_at(now, CAPTURE_DONE), _store,
            note="IBKR's API shows only the current day's executions: a missed evening can't be had later",
            off_because=_store_off),
)  # fmt: skip


def check(dataset: Dataset, now: datetime, root: Path, profile: str) -> Status:
    expected = dataset.expected(now)
    if dataset.off_because is not None:
        try:
            off = dataset.off_because()
        except Exception as exc:  # noqa: BLE001
            off = f"can't tell: {type(exc).__name__}: {exc}"
        if off:
            return Status(dataset, None, expected, "off", off)
    try:
        mark = dataset.watermark(root, profile)
    except Exception as exc:  # noqa: BLE001 — an unreadable dataset is one to look at, not a crash
        return Status(dataset, None, expected, "missing", f"can't read it: {type(exc).__name__}: {exc}")
    if mark is None or mark.through is None:
        return Status(dataset, mark, expected, "missing", "nothing recorded here yet")
    if expected is None or mark.through >= expected:
        return Status(dataset, mark, expected, "ok", f"through {mark.through}")
    behind = len(sessions_between(mark.through, expected, limit=1_000))
    return Status(dataset, mark, expected, "behind",
                  f"through {mark.through}; should be through {expected} ({behind} session{'s' * (behind != 1)} behind)")  # fmt: skip


def status(now: datetime | None = None, root: Path = PROJECT_ROOT, profile: str = "paper") -> list[Status]:
    now = now or datetime.now(timezone.utc)
    return [check(d, now, root, profile) for d in DATASETS]


# ── catching up ──────────────────────────────────────────────────────────


def busy(now: datetime) -> bool:
    """While the run, the capture and the archive write the same files."""
    local = now.astimezone(NY)
    return clock.is_us_business_day(local.date()) and BUSY[0] <= local.time() < BUSY[1]


def catchup(now: datetime | None = None, root: Path = PROJECT_ROOT, profile: str = "paper",
            only: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """Fill every dataset that is behind (or missing) and has a catch-up, then
    record before, after and what each catch-up said in artifacts/catchup/."""
    now = now or datetime.now(timezone.utc)
    if busy(now):
        return {"ok": False, "refused": "between 15:10 and 17:45 ET the daily jobs write these files: try after"}
    from src.broker.ibkr import settings

    endpoint = settings.resolve_endpoint(profile)
    before = status(now, root, profile)
    missing = {
        s.dataset.name: sessions_between(s.watermark.through if s.watermark else None, s.expected)
        for s in before if s.state in ("behind", "missing") and s.expected
    }  # fmt: skip
    ctx = {"profile": profile, "host": endpoint.host, "port": endpoint.port,
           "client_id": settings.client_id_for("backfill", profile), "missing": missing}  # fmt: skip
    actions = []
    for s in before:
        if s.state not in ("behind", "missing") or (only and s.dataset.name not in only):
            continue
        if s.dataset.catchup is None:
            actions.append({"dataset": s.dataset.name, "ok": False, "detail": f"no catch-up: {s.dataset.note}"})
            continue
        try:
            result = s.dataset.catchup(ctx)
            ok = bool(result.get("ok"))
            detail = str(result.get("error") or result.get("skipped") or result.get("detail") or "")
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        actions.append({"dataset": s.dataset.name, "ok": ok, "detail": detail})
    after = status(now, root, profile)
    record = {
        "at": now.isoformat(timespec="seconds"),
        "profile": profile,
        "actions": actions,
        "before": [s.as_dict() for s in before],
        "after": [s.as_dict() for s in after],
    }
    folder = root / "artifacts" / "catchup"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"catchup_{profile}_{now:%Y%m%dT%H%M%SZ}.json").write_text(json.dumps(record, indent=2))
    record["ok"] = all(a["ok"] for a in actions)
    return record


def last_catchup(root: Path = PROJECT_ROOT, profile: str = "paper") -> dict[str, Any] | None:
    files = sorted((root / "artifacts" / "catchup").glob(f"catchup_{profile}_*.json"))
    try:
        return json.loads(files[-1].read_text()) if files else None
    except (OSError, ValueError):
        return None


def render(rows: list[Status], now: datetime) -> str:
    lines = [f"Datasets at {now.astimezone(NY):%Y-%m-%d %H:%M} ET (watermark against what should exist by now)", ""]
    width = max(len(s.dataset.title) for s in rows)
    for s in rows:
        lines.append(f"  {s.state.upper():8}{s.dataset.title:<{width}}  {s.detail}")
    behind = [s.dataset.title for s in rows if s.state in ("behind", "missing")]
    lines += ["", f"{len(rows) - len(behind)} of {len(rows)} up to date."
              + (" bin/tradeit catchup fills what it can." if behind else "")]  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import logging

    parser = argparse.ArgumentParser(description="Each dataset's watermark, and the catch-up that fills it.")
    parser.add_argument("--profile", default="paper")
    parser.add_argument("--catchup", action="store_true", help="fill the datasets that are behind")
    parser.add_argument("--only", nargs="*", default=(), help="catch up only these datasets (names)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    now = datetime.now(timezone.utc)
    if args.catchup:
        record = catchup(now, profile=args.profile, only=tuple(args.only))
        if record.get("refused"):
            print(f"Not now: {record['refused']}")
            return 1
        for a in record["actions"]:
            print(f"  {'filled' if a['ok'] else 'NOT filled':10} {a['dataset']}: {a['detail']}")
        if not record["actions"]:
            print("Nothing was behind.")
        print()
    rows = status(datetime.now(timezone.utc), profile=args.profile)
    if args.json:
        print(json.dumps([s.as_dict() for s in rows], indent=2))
    else:
        print(render(rows, datetime.now(timezone.utc)))
    return 0 if all(s.state in ("ok", "off") for s in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
