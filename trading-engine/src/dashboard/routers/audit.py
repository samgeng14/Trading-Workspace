"""
audit.py - System Audit routes.

Runs the suite of file-freshness, pipeline-health and automation-chain checks
that used to live in ``screens/system_audit.py`` and returns the result as
JSON. The frontend renders the table.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter

from src.dashboard._cache import cache_data
from src.dashboard.routers._helpers import get_profile


router = APIRouter()
_ROOT = Path(__file__).resolve().parents[3]


def _check(name: str, status: str, detail: str, *, severity: str = "info") -> dict[str, Any]:
    return {
        "check": name,
        "status": status,
        "detail": detail,
        "severity": severity,
    }


def _file_age(path: Path) -> tuple[float | None, str]:
    if not path.exists():
        return None, "missing"
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age_h = (datetime.now() - mtime).total_seconds() / 3600.0
        return age_h, mtime.strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None, "unreadable"


def _check_master_prices() -> dict[str, Any]:
    base = _ROOT / "data" / "market" / "cleaned" / "prices"
    parquet = base / "etf_prices_master.parquet"
    csv = base / "etf_prices_master.csv"
    path = parquet if parquet.exists() else (csv if csv.exists() else None)
    if path is None:
        return _check("Master price file", "fail", "No master price file on disk", severity="critical")
    try:
        df = (
            pd.read_parquet(path) if path.suffix == ".parquet"
            else pd.read_csv(path, usecols=["date", "symbol"])
        )
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        latest = df["date"].max().date()
        n_symbols = df["symbol"].nunique()
        age = (datetime.now().date() - latest).days
        detail = f"{len(df):,} rows; {n_symbols} symbols; latest {latest} ({age}d ago)"
        if age <= 1:
            return _check("Master price file", "pass", detail)
        if age <= 7:
            return _check("Master price file", "warn", detail, severity="minor")
        return _check("Master price file", "fail", detail, severity="critical")
    except Exception as exc:  # noqa: BLE001
        return _check("Master price file", "fail", f"Read failed: {exc}", severity="critical")


def _check_targets() -> dict[str, Any]:
    base = _ROOT / "data" / "market" / "cleaned" / "targets"
    parquet = base / "etf_targets_monthly.parquet"
    csv = base / "etf_targets_monthly.csv"
    path = parquet if parquet.exists() else (csv if csv.exists() else None)
    if path is None:
        return _check("Targets file", "fail", "No targets file", severity="critical")
    try:
        df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        date_col = "rebalance_date" if "rebalance_date" in df.columns else "date"
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        latest = df[date_col].max().date()
        age = (datetime.now().date() - latest).days
        n_picks = df[df[date_col] == df[date_col].max()].shape[0]
        detail = f"latest rebalance {latest} ({age}d ago); {n_picks} picks"
        if age <= 31:
            return _check("Targets file", "pass", detail)
        return _check("Targets file", "warn", detail, severity="minor")
    except Exception as exc:  # noqa: BLE001
        return _check("Targets file", "fail", f"Read failed: {exc}", severity="critical")


def _check_positions(profile: str) -> dict[str, Any]:
    path = _ROOT / "data" / "broker" / "positions" / f"{profile}_positions_snapshot.csv"
    age_h, ts = _file_age(path)
    if age_h is None:
        return _check("Positions snapshot", "fail", f"File {ts}", severity="critical")
    try:
        df = pd.read_csv(path)
        n = len(df)
    except Exception as exc:  # noqa: BLE001
        return _check("Positions snapshot", "fail", f"Read failed: {exc}", severity="critical")
    detail = f"{n} rows; last write {ts} ({age_h:.1f}h ago)"
    if age_h <= 24:
        return _check("Positions snapshot", "pass", detail)
    if age_h <= 72:
        return _check("Positions snapshot", "warn", detail, severity="minor")
    return _check("Positions snapshot", "fail", detail, severity="critical")


def _check_account_summary(profile: str) -> dict[str, Any]:
    folder = _ROOT / "data" / "broker" / "account"
    files = sorted(folder.glob(f"{profile}_account_summary_*.csv"))
    if not files:
        return _check("Account summary", "fail", "No snapshots", severity="critical")
    age_h, ts = _file_age(files[-1])
    detail = f"{len(files)} snapshots; last {ts} ({age_h:.1f}h ago)" if age_h else "unreadable"
    if age_h is None:
        return _check("Account summary", "fail", detail, severity="critical")
    if age_h <= 24:
        return _check("Account summary", "pass", detail)
    if age_h <= 72:
        return _check("Account summary", "warn", detail, severity="minor")
    return _check("Account summary", "fail", detail, severity="critical")


def _check_runner_log() -> dict[str, Any]:
    folder = _ROOT / "data" / "logs" / "runtime"
    files = sorted(folder.glob("daily_runner_*.log"))
    if not files:
        return _check("Daily runner log", "warn", "No runs yet", severity="minor")
    age_h, ts = _file_age(files[-1])
    if age_h is None:
        return _check("Daily runner log", "fail", "Last log unreadable", severity="critical")
    try:
        tail = files[-1].read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
    except OSError:
        tail = []
    failed = any("Append failed" in line or "FAIL" in line for line in tail)
    completed = any(
        "DAILY RUNNER COMPLETE" in line or "No rebalance needed today" in line
        for line in tail
    )
    detail = f"last log {ts} ({age_h:.1f}h ago)"
    if failed:
        return _check("Daily runner", "fail", detail + "; last run failed", severity="critical")
    if not completed:
        return _check("Daily runner", "warn", detail + "; last run did not complete", severity="minor")
    if age_h <= 36:
        return _check("Daily runner", "pass", detail + "; last run OK")
    return _check("Daily runner", "warn", detail + "; older than 36h", severity="minor")


def _check_launchagent() -> dict[str, Any]:
    plist = Path.home() / "Library" / "LaunchAgents" / "com.capitalfund.daily-runner.plist"
    if not plist.exists():
        return _check(
            "LaunchAgent plist",
            "warn",
            "Not installed - automation will not fire. Run: "
            "bin/tradeit schedule install",
            severity="minor",
        )
    try:
        uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
        result = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/com.capitalfund.daily-runner"],
            capture_output=True, text=True, timeout=5,
        )
        loaded = result.returncode == 0
    except Exception:  # noqa: BLE001
        loaded = False
    if not loaded:
        return _check(
            "LaunchAgent plist",
            "warn",
            f"Plist on disk but not loaded. Run: launchctl bootstrap gui/$(id -u) {plist}",
            severity="minor",
        )
    age_h, ts = _file_age(plist)
    return _check("LaunchAgent loaded", "pass", f"plist at {ts}; registered with launchctl")


def _check_sleep_settings() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["pmset", "-g", "sched"], capture_output=True, text=True, timeout=5,
        )
        sched = result.stdout
    except Exception as exc:  # noqa: BLE001
        return _check("Wake schedule", "warn", f"Could not read pmset: {exc}", severity="minor")
    if "wakeorpoweron" in sched.lower() or "wake at" in sched.lower():
        return _check(
            "Wake schedule", "pass", "pmset has a wake schedule; Mac will wake on its own"
        )
    return _check(
        "Wake schedule",
        "warn",
        "No wake schedule. Set one five minutes before the daily run, in this Mac's time "
        "(14:30 in Central time): sudo pmset repeat wakeorpoweron MTWRF 14:30:00",
        severity="minor",
    )


def _check_autologin() -> dict[str, Any]:
    autologin = Path("/etc/kcpassword")
    if autologin.exists():
        return _check("Auto-login", "pass", "Configured (kcpassword present)")
    return _check(
        "Auto-login",
        "warn",
        "Not configured. Enable: System Settings > Users and Groups > Automatically log in as.",
        severity="minor",
    )


def _check_runner_python_deps() -> dict[str, Any]:
    plist = Path.home() / "Library" / "LaunchAgents" / "com.capitalfund.daily-runner.plist"
    if not plist.exists():
        return _check("Runner Python deps", "warn", "Plist missing", severity="minor")
    try:
        text = plist.read_text(errors="ignore")
    except OSError:
        return _check("Runner Python deps", "warn", "Plist unreadable", severity="minor")
    py_path: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("<string>") and s.endswith("python</string>"):
            py_path = s.removeprefix("<string>").removesuffix("</string>")
            break
    if not py_path or not Path(py_path).exists():
        return _check(
            "Runner Python deps", "warn",
            f"Plist points at {py_path or '?'} which is missing", severity="minor",
        )
    missing: list[str] = []
    for mod in ("pandas", "pyarrow", "ib_async"):
        try:
            r = subprocess.run(
                [py_path, "-c", f"import {mod}"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                missing.append(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    if not missing:
        return _check(
            "Runner Python deps",
            "pass",
            f"{Path(py_path).parent.name}/python has pandas, pyarrow, ib_async",
        )
    return _check(
        "Runner Python deps",
        "fail",
        f"Missing: {missing}. Install: {py_path} -m pip install {' '.join(missing)}",
        severity="critical",
    )


def _check_next_fire_time() -> dict[str, Any]:
    now = datetime.now()
    fire_hour, fire_minute = 16, 32
    candidate = now.replace(hour=fire_hour, minute=fire_minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    delta = candidate - now
    hours = delta.total_seconds() / 3600.0
    return _check(
        "Next scheduled run",
        "pass",
        f"{candidate.strftime('%a %Y-%m-%d %H:%M')} (in {hours:.1f}h)",
    )


def _check_ibkr_reachable() -> dict[str, Any]:
    port_map = {
        7497: "TWS paper",
        4002: "IB Gateway paper",
        4001: "IB Gateway live",
        7496: "TWS live",
    }
    found = []
    for port, label in port_map.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.4)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                found.append(f"{label} ({port})")
        finally:
            sock.close()
    if found:
        return _check("IBKR API listener", "pass", "; ".join(found))
    return _check(
        "IBKR API listener",
        "warn",
        "Nothing listening on 7497/4002/4001/7496 - TWS or IB Gateway not running",
        severity="minor",
    )


def _check_stale_imports() -> dict[str, Any]:
    critical_modules = [
        "src.production.runtime.build_paper_basket",
        "src.production.daily_runner",
        "src.dashboard.utils.data_loaders",
        "src.dashboard.app",
    ]
    stale = []
    for mod_name in critical_modules:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        src_file = getattr(mod, "__file__", None)
        if not src_file or not os.path.exists(src_file):
            continue
        try:
            disk_mtime = os.stat(src_file).st_mtime
        except OSError:
            continue
        pycache = os.path.join(os.path.dirname(src_file), "__pycache__")
        loaded_mtime = None
        if os.path.isdir(pycache):
            stem = os.path.splitext(os.path.basename(src_file))[0]
            for f in os.listdir(pycache):
                if f.startswith(stem + ".") and f.endswith(".pyc"):
                    try:
                        loaded_mtime = os.stat(os.path.join(pycache, f)).st_mtime
                        break
                    except OSError:
                        continue
        if loaded_mtime is None:
            continue
        if disk_mtime > loaded_mtime + 5:
            stale.append(mod_name.rsplit(".", 1)[-1])
    if not stale:
        return _check("Module freshness", "pass", "All critical modules match disk")
    return _check(
        "Module freshness",
        "warn",
        "Stale modules: " + ", ".join(stale) + ". Restart uvicorn to pick up disk edits.",
        severity="minor",
    )


def _check_positions_match_targets(profile: str) -> dict[str, Any]:
    pos_path = _ROOT / "data" / "broker" / "positions" / f"{profile}_positions_snapshot.csv"
    base = _ROOT / "data" / "market" / "cleaned" / "targets"
    targets_path = base / "etf_targets_monthly.parquet"
    if not targets_path.exists():
        targets_path = base / "etf_targets_monthly.csv"
    if not pos_path.exists() or not targets_path.exists():
        return _check("Targets vs positions", "warn", "Missing one of the two files", severity="minor")
    try:
        pos = pd.read_csv(pos_path)
        if targets_path.suffix == ".parquet":
            tgt = pd.read_parquet(targets_path)
        else:
            tgt = pd.read_csv(targets_path)
    except Exception as exc:  # noqa: BLE001
        return _check("Targets vs positions", "fail", f"Read failed: {exc}", severity="minor")
    pos_syms = set(pos["symbol"].astype(str).str.upper().str.strip())
    date_col = "rebalance_date" if "rebalance_date" in tgt.columns else "date"
    tgt[date_col] = pd.to_datetime(tgt[date_col], errors="coerce")
    latest_tgt = tgt[tgt[date_col] == tgt[date_col].max()]
    tgt_syms = set(latest_tgt["symbol"].astype(str).str.upper().str.strip())
    extra = pos_syms - tgt_syms
    missing = tgt_syms - pos_syms
    if not extra and not missing:
        return _check(
            "Targets vs positions", "pass",
            f"All {len(tgt_syms)} target symbols are held",
        )
    parts: list[str] = []
    if missing:
        parts.append(f"missing {sorted(missing)}")
    if extra:
        parts.append(f"extra {sorted(extra)}")
    return _check("Targets vs positions", "warn", "; ".join(parts), severity="minor")


@router.get("/run")
def run(profile: str | None = None, refresh: bool = False) -> dict[str, Any]:
    profile = profile or get_profile()
    if refresh:
        cache_data.clear()
    checks = [
        _check_master_prices(),
        _check_targets(),
        _check_positions(profile),
        _check_account_summary(profile),
        _check_runner_log(),
        _check_runner_python_deps(),
        _check_ibkr_reachable(),
        _check_positions_match_targets(profile),
        _check_stale_imports(),
        _check_launchagent(),
        _check_sleep_settings(),
        _check_autologin(),
        _check_next_fire_time(),
    ]
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "profile": profile,
        "checks": checks,
        "counts": counts,
        "total": len(checks),
    }
