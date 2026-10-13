"""Owner-confirmed genuine moves (config/confirmed_moves.yaml).

A one-day move the size of a split ratio is treated as bad data: the merge
refuses it, the repair tries to refetch it, the validator blocks trading. A
real move that large (ETH fell about 42% on 2020-03-12) would block forever,
so the owner can confirm exactly that symbol and date, after seeing the bars.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.cleaning import price_basis as pb  # noqa: E402
from src.data.cleaning.validators import validate_master_prices  # noqa: E402

DAY = "2026-03-12"
MOVE = [("ETHA", DAY)]


def etha(start: str = "2025-06-02", end: str = "2026-10-09", *, drop: float = 0.55) -> pd.DataFrame:
    """ETHA bars with a genuine crash on DAY: the close falls 45% and stays down (x0.55 is
    within 15% of a 2-for-1 split, so the checks read it as one)."""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(11)
    close = 20.0 * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
    close[dates >= pd.Timestamp(DAY)] *= drop
    df = pd.DataFrame({"date": dates, "symbol": "ETHA", "close": close})
    df["open"], df["high"], df["low"], df["volume"] = df["close"], df["close"] * 1.01, df["close"] * 0.99, 1e6
    return df[pb.COLUMNS]


@pytest.fixture(autouse=True)
def owners_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The owner's file, as committed (no moves yet), at a temp path."""
    path = tmp_path / "confirmed_moves.yaml"
    path.write_text((PROJECT_ROOT / "config" / "confirmed_moves.yaml").read_text())
    monkeypatch.setattr(pb, "CONFIRMED_MOVES_PATH", path)
    return path


def test_the_committed_file_confirms_nothing() -> None:
    assert pb.load_confirmed_moves(PROJECT_ROOT / "config" / "confirmed_moves.yaml") == frozenset()


# ── the validator ────────────────────────────────────────────────────────────

def test_an_unconfirmed_move_still_blocks_and_says_how_to_confirm_it() -> None:
    v = validate_master_prices(etha())
    assert v.ok is False and len(v.errors) == 1
    msg = v.errors[0]
    assert "ETHA 2026-03-12" in msg and "2-for-1 split" in msg and "bin/tradeit refresh" in msg
    assert "python -m src.production.backfill_gaps --confirm-move ETHA 2026-03-12" in msg


def test_a_confirmed_move_passes_the_validator(owners_file: Path) -> None:
    assert validate_master_prices(etha(), confirmed=MOVE).errors == []
    # through the owner's file, as the daily runner's gate reads it
    assert pb.add_confirmed_move("ETHA", DAY, "ETH sold off with crypto") is True
    v = validate_master_prices(etha())
    assert v.ok is True and v.errors == []
    # exactly that day: the same move a day later still blocks
    later = etha()
    later.loc[later["date"] == pd.Timestamp(DAY), "close"] /= 0.55
    v = validate_master_prices(later)
    assert v.ok is False and "ETHA 2026-03-13" in v.errors[0]


def test_a_confirmed_move_is_not_reported_as_suspicious_either() -> None:
    crash = etha(drop=0.4)  # -60%: past the 50% warning line
    v = validate_master_prices(crash, confirmed=MOVE)
    assert v.errors == [] and not any("suspicious" in w for w in v.warnings)


# ── the merge and the repair ─────────────────────────────────────────────────

def test_the_merge_refuses_an_unconfirmed_move_and_takes_a_confirmed_one() -> None:
    full = etha()
    master = full[full["date"] < "2026-03-01"]
    new = full[full["date"] >= "2026-02-20"]
    refetch = lambda _symbol: full  # noqa: E731  (IBKR's history has the same crash)

    report = pb.merge_into_master(master, new, refetch)
    (refused,) = report.refused
    assert refused["symbol"] == "ETHA" and "bad jumps of its own" in refused["reason"]
    assert "--confirm-move ETHA 2026-03-12" in refused["reason"]
    assert report.master["date"].max() < pd.Timestamp("2026-03-01")  # nothing new merged

    report = pb.merge_into_master(master, new, refetch, confirmed=MOVE)
    assert report.results["ETHA"].status == "merged" and report.refused == []
    assert report.master["date"].max() == pd.Timestamp("2026-10-09")


def test_the_repair_leaves_a_confirmed_move_alone() -> None:
    full = etha()
    report = pb.repair_master_basis(full, refetch=lambda _s: full)
    assert report.results["ETHA"].status == "refused"
    assert "--confirm-move ETHA 2026-03-12" in report.results["ETHA"].reason

    report = pb.repair_master_basis(full, refetch=lambda _s: full, confirmed=MOVE)
    assert report.results["ETHA"].status == "unchanged"


def test_a_typo_in_the_file_is_an_error_not_a_silent_skip(owners_file: Path) -> None:
    owners_file.write_text("moves:\n  - symbol: ETHA\n    day: 2026-03-12\n")
    with pytest.raises(ValueError, match="entry 1 needs a symbol and a date"):
        pb.load_confirmed_moves()
    owners_file.write_text("moves:\n  - symbol: ETHA\n    date: 2026-13-45\n")
    with pytest.raises(ValueError, match="confirmed_moves.yaml can't be read: month must be in 1..12"):
        pb.load_confirmed_moves()
    # the entries without the moves: key above them, or one entry instead of a list
    for text in ("- symbol: ETHA\n  date: 2026-03-12\n", "moves:\n  symbol: ETHA\n  date: 2026-03-12\n"):
        owners_file.write_text(text)
        with pytest.raises(ValueError, match="needs a moves: list of entries"):
            pb.load_confirmed_moves()
    # and the validator, which reads it, fails rather than passing a move nobody confirmed
    with pytest.raises(ValueError):
        validate_master_prices(etha())


# ── the command that adds one ────────────────────────────────────────────────

def test_confirm_move_shows_the_bars_then_records_it(owners_file: Path) -> None:
    from src.production.backfill_gaps import confirm_move

    header = owners_file.read_text().split("moves:")[0]
    said: list[str] = []
    code = confirm_move("etha", DAY, "ETH sold off with crypto", yes=True, master=etha(), say=said.append)

    assert code == 0
    text = "\n".join(said)
    assert "ETHA around 2026-03-12 (price master):" in text
    assert len([line for line in said if line.startswith("  20")]) == 11  # five sessions either side
    (marked,) = [line for line in said if line.endswith("<- this move")]
    assert marked.startswith("  2026-03-12") and re.search(r" -4\d\.\d%", marked)
    assert "2-for-1 split" in text
    assert ("ETHA", pd.Timestamp(DAY)) in pb.load_confirmed_moves()
    saved = owners_file.read_text()
    assert saved.startswith(header.rstrip())  # the comments are kept
    assert "symbol: ETHA" in saved and "note: ETH sold off with crypto" in saved

    said.clear()
    assert confirm_move("ETHA", DAY, "again", yes=True, master=etha(), say=said.append) == 0
    assert said[-1] == "ETHA 2026-03-12 is already confirmed."
    assert saved == owners_file.read_text()


def test_confirm_move_asks_first(owners_file: Path) -> None:
    from src.production.backfill_gaps import confirm_move

    before = owners_file.read_text()
    answers = iter(["no"])
    assert confirm_move("ETHA", DAY, master=etha(), ask=lambda _q: next(answers), say=lambda _s: None) == 1
    assert owners_file.read_text() == before

    answers = iter(["yes", "ETH sold off"])
    assert confirm_move("ETHA", DAY, master=etha(), ask=lambda _q: next(answers), say=lambda _s: None) == 0
    assert ("ETHA", pd.Timestamp(DAY)) in pb.load_confirmed_moves()


def test_confirm_move_refuses_a_day_with_no_bar(owners_file: Path) -> None:
    from src.production.backfill_gaps import confirm_move

    said: list[str] = []
    assert confirm_move("ETHA", "2026-03-14", "x", yes=True, master=etha(), say=said.append) == 1
    assert said == ["ETHA has no bar on 2026-03-14 in the price master; nothing to confirm."]
    assert confirm_move("ETHA", DAY, None, yes=True, master=etha(), say=said.append) == 2  # a note is required
    assert pb.load_confirmed_moves() == frozenset()


def test_the_cli_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owners_file: Path) -> None:
    from src.production import backfill_gaps

    master = tmp_path / "etf_prices_master.parquet"
    etha().to_parquet(master, index=False)
    monkeypatch.setattr(backfill_gaps, "MASTER_PARQUET", master)
    monkeypatch.setattr(
        sys, "argv", ["backfill_gaps", "--confirm-move", "ETHA", DAY, "--yes", "--note", "ETH sold off"]
    )
    with pytest.raises(SystemExit) as exit_:
        backfill_gaps.main()
    assert exit_.value.code == 0
    assert ("ETHA", pd.Timestamp(DAY)) in pb.load_confirmed_moves()
