"""Shadow mode for R3, short the VIX futures premium (src/production/shadow_r3.py):
the rule run forward on Cboe's settlements from the shadow's start, mixed half and
half with the live account, the short it would hold; the Cboe fetch it runs on
(src/data/ingest/cboe_vx.py); and the evening runner for every shadow book
(src/production/shadows.py)."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.walkforward")
from src.data.ingest import cboe_vx  # noqa: E402
from src.production import shadow_r3, shadows  # noqa: E402
from src.strategies import trend  # noqa: E402
from src.strategies import vix_premium as vp  # noqa: E402

SYMBOLS = [*trend.RISKY, trend.CASH]
START = date(2026, 3, 2)
HEADER = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest"


def monthly_expiries(n: int = 14) -> list[pd.Timestamp]:
    out = []
    for k in range(n):
        d = pd.Timestamp(2025 + (k + 9) // 12, (k + 9) % 12 + 1, 15)
        out.append(d + pd.Timedelta(days=(2 - d.weekday()) % 7))  # a Wednesday mid-month, from October 2025
    return out


def write_cboe(root: Path, days: pd.DatetimeIndex, rng: np.random.Generator) -> pd.Series:
    """Cboe-format files for monthly contracts, and the VIX history; returns the spot path."""
    spot = pd.Series(np.clip(17 + np.cumsum(rng.normal(0, 0.7, len(days))), 10, 40), index=days)
    folder = cboe_vx.folder(root) / "vx"
    folder.mkdir(parents=True)
    for e in monthly_expiries():
        rows = []
        for t in days[(days >= e - pd.Timedelta(days=270)) & (days <= e)]:
            months = (e - t).days / 30.0
            settle = round(float(spot[t]) + 1.0 * np.sqrt(max(months, 0.0)) + rng.normal(0, 0.1), 4)
            rows.append(f"{t.date()},X ({e:%b %Y}),{settle},{settle + 0.3},{settle - 0.3},{settle},{settle},0,1000,0,5000")
        (folder / f"VX_{e.date()}.csv").write_text("\n".join([HEADER, *rows]) + "\n")
    vix = spot + rng.normal(0, 1.2, len(days))  # crosses the 30-day price both ways
    lines = [f"{t:%m/%d/%Y},{v:.2f},{v + 0.5:.2f},{v - 0.5:.2f},{v:.2f}" for t, v in vix.items()]
    cboe_vx.vix_path(root).write_text("DATE,OPEN,HIGH,LOW,CLOSE\n" + "\n".join(lines) + "\n")
    return spot


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    from src.common import clock

    rng = np.random.default_rng(11)
    # NYSE's sessions, as Cboe's settlements fall (CFE closes on NYSE holidays, a couple of days aside)
    days = pd.DatetimeIndex([d for d in pd.bdate_range("2025-06-02", "2026-04-30") if clock.is_us_business_day(d.date())])
    steps = rng.normal(0.0001, 0.002, (len(days), len(SYMBOLS)))
    panel = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=days, columns=SYMBOLS)
    prices = tmp_path / "data" / "market" / "cleaned" / "prices"
    prices.mkdir(parents=True)
    master = panel.stack().rename("close").reset_index().rename(columns={"level_0": "date", "level_1": "symbol"})
    master["volume"] = 5_000_000.0
    master.to_parquet(prices / "etf_prices_master.parquet")
    master.rename(columns={"close": "adj_close"})[["date", "symbol", "adj_close"]].to_parquet(
        prices / "etf_total_return_closes.parquet")  # fmt: skip
    api = tmp_path / "artifacts" / "api"
    api.mkdir(parents=True)
    navs = 1_000_000 * np.cumprod(1 + rng.normal(0.0003, 0.008, len(days)))
    (api / "nav_paper.csv").write_text("date,nav\n" + "\n".join(f"{d.date()},{v:.2f}" for d, v in zip(days, navs)) + "\n")
    write_cboe(tmp_path, days, rng)
    return tmp_path


def test_nothing_before_the_shadow_starts(tree) -> None:
    out = shadow_r3.run(root=tree, start=date(2026, 4, 30), fetch=False)
    assert out == {"ok": True, "skipped": "the shadow starts on 2026-04-30; prices run to 2026-04-30"}


def test_the_rule_runs_forward_and_mixes_half_and_half_with_the_live_account(tree) -> None:
    out = shadow_r3.run(now=datetime(2026, 4, 30, 21, 5, tzinfo=timezone.utc), root=tree, start=START, fetch=False)
    assert out["ok"] and out["session"] == "2026-04-30" and out["sessions"] > 30
    daily = pd.read_csv(tree / "artifacts" / "shadow" / "r3" / "daily.csv", index_col="date", parse_dates=True)
    assert daily.index[0] > pd.Timestamp(START)  # decided at the start's close, traded the next session
    mixed = 0.5 * daily["sleeve_return"] + 0.5 * daily["live_return"]
    pd.testing.assert_series_equal(daily["book_return"], mixed, check_names=False)

    # every decision is the researched rule's, on the same 30-day series and VIX
    table, cm, vix = shadow_r3.series(tree)
    panel, volumes = shadow_r3.price_panel(tree, cm)
    events = shadow_r3.sleeve(panel, volumes, cm, vix, START).events
    rule = vp.rule(cm, vix)
    for row in events.itertuples():
        expected = rule(pd.DataFrame(index=panel.index[panel.index <= row.decided]))
        assert (getattr(row, vp.INSTRUMENT) or 0.0) == expected.get(vp.INSTRUMENT, 0.0), row.decided
    assert {getattr(r, vp.INSTRUMENT) for r in events.itertuples()} >= {0.0, -vp.NOTIONAL}  # it went both ways

    s = out["signal"]
    assert s["date"] == "2026-04-30" and s["contango"] == (s["cm30_price"] > s["vix"])
    kept = json.loads((tree / "artifacts" / "shadow" / "r3" / "2026-04-30.json").read_text())
    assert kept["recorded_at"] == "2026-04-30T21:05:00+00:00" and kept["signal"] == s
    if s["contango"]:  # the short split over the roll's two months, 10% of the live NAV in all
        nav = float(pd.read_csv(tree / "artifacts" / "api" / "nav_paper.csv")["nav"].iloc[-1])
        assert sum(h["notional"] for h in out["would_hold"]) == pytest.approx(-vp.NOTIONAL * nav, abs=0.02)
        assert all(h["vxm"] <= 0 for h in out["would_hold"])
    else:
        assert out["would_hold"] == []


def test_the_series_starts_where_the_front_month_is_cached(tree) -> None:
    table, cm, _ = shadow_r3.series(tree)
    first = min(table.columns)
    assert table.index[0] >= first - shadow_r3.FRONT_KNOWN
    assert (cm["front"] == first).loc[: first - pd.Timedelta(days=1)].all()


# ── the Cboe fetch ───────────────────────────────────────────────────────


def fake_cboe(listing: dict, files: dict[str, bytes]):
    calls: list[str] = []

    def fetch_url(url: str) -> bytes | None:
        calls.append(url)
        if url == cboe_vx.LIST_URL:
            return json.dumps(listing).encode()
        if url == cboe_vx.VIX_URL:
            return b"DATE,OPEN,HIGH,LOW,CLOSE\n10/21/2026,17.8,18.4,17.2,17.35\n"
        return files.get(url.removeprefix("https://cdn.cboe.com/"))

    return fetch_url, calls


def entry(expiry: str) -> dict:
    return {"duration_type": "M", "expire_date": expiry, "path": f"data/x/VX/VX_{expiry}.csv"}


def test_the_fetch_keeps_expired_files_and_refreshes_listed_ones(tmp_path) -> None:
    row = "2026-10-21,X (Nov 2026),18.25,18.65,18.10,18.24,18.2382,-0.25,91982,0,218255"
    listing = {"2025": [entry("2025-03-18")], "2026": [entry("2026-09-16"), entry("2026-11-18"),
               {"duration_type": "W", "expire_date": "2026-10-28", "path": "data/x/VX/VX_weekly.csv"}]}  # fmt: skip
    files = {e["path"]: f"{HEADER}\n{row}\n".encode() for year in listing.values() for e in year}
    fetch_url, calls = fake_cboe(listing, files)
    out = cboe_vx.fetch(tmp_path, today=date(2026, 10, 22), fetch_url=fetch_url)
    names = sorted(p.name for p in cboe_vx.contract_paths(tmp_path))
    assert names == ["VX_2026-09-16.csv", "VX_2026-11-18.csv"]  # too old and weekly ones are skipped
    assert out["contracts_fetched"] == 2 and out["settlements_through"] == "2026-10-21"
    calls.clear()
    out = cboe_vx.fetch(tmp_path, today=date(2026, 10, 23), fetch_url=fetch_url)
    assert out["contracts_kept"] == 1 and not any("2026-09-16" in c for c in calls)  # expired: fetched once


def test_a_web_page_is_refused_not_cached(tmp_path) -> None:
    fetch_url, _ = fake_cboe({"2026": [entry("2026-11-18")]}, {"data/x/VX/VX_2026-11-18.csv": b"<!DOCTYPE html><p>busy"})
    with pytest.raises(RuntimeError, match="web page"):
        cboe_vx.fetch(tmp_path, today=date(2026, 10, 22), fetch_url=fetch_url)
    assert cboe_vx.contract_paths(tmp_path) == []


# ── every shadow book, each evening ──────────────────────────────────────


def test_one_book_failing_doesnt_stop_the_other(monkeypatch, capsys) -> None:
    ran = []

    def book(name: str, code: int):
        def main() -> int:
            ran.append(name)
            if code == 2:
                raise ValueError("no prices")
            return code

        return main

    monkeypatch.setattr("src.production.shadow_h2.main", book("h2", 2))
    monkeypatch.setattr("src.production.shadow_r3.main", book("r3", 0))
    assert shadows.main() == 1 and ran == ["h2", "r3"]
    assert "shadow h2 failed: ValueError: no prices" in capsys.readouterr().out
    monkeypatch.setattr("src.production.shadow_h2.main", book("h2", 0))
    assert shadows.main() == 0


def test_the_roll_weights_stay_put_as_later_settlements_arrive(tree, tmp_path_factory) -> None:
    """The weights the shadow gives the last date are the ones the full period gives:
    cut the files a month short, and every weight up to the cut matches the full run."""
    import shutil

    full_table, full, _ = shadow_r3.series(tree)
    cut = pd.Timestamp("2026-03-31")
    short = tmp_path_factory.mktemp("short")
    shutil.copytree(cboe_vx.folder(tree), cboe_vx.folder(short))
    for path in cboe_vx.contract_paths(short):
        lines = path.read_text().splitlines()
        path.write_text("\n".join([lines[0], *[ln for ln in lines[1:] if pd.Timestamp(ln[:10]) <= cut]]) + "\n")
    _, partial, _ = shadow_r3.series(short)
    assert partial.index[-1] == cut and pd.notna(partial["w_first"].iloc[-1])  # the last date has its weights
    both = partial.index.intersection(full.index)
    cols = ["first", "second_roll", "w_first", "er_return"]
    pd.testing.assert_frame_equal(partial.loc[both, cols], full.loc[both, cols], check_exact=True)
