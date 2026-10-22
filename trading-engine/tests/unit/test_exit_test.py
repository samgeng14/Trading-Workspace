"""Phase 0's exit test, session by session (src/runtime/exit_test.py): each
session's doctor, run, reconcile and NAV, and the streak of sessions that
passed all four."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import exit_test as et  # noqa: E402

TODAY = date(2026, 10, 21)  # a Wednesday


class Tree:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.a = root / "artifacts"
        for sub in ("runs", "doctor", "api"):
            (self.a / sub).mkdir(parents=True, exist_ok=True)
        self.close: dict[str, float] = {}
        self.flex: dict[str, float] = {}

    def run(self, day: str, stamp: str = "193500", *, ok: bool = True, diffs: int = 0, **extra) -> None:
        doc = {"run_id": f"{day.replace('-', '')}_{stamp}", "profile": "paper", "ok": ok,
               "steps": {"reconcile": {"ok": True, "material_mismatch": diffs > 0, "diffs": [{}] * diffs}}, **extra}
        (self.a / "runs" / f"daily_run_{doc['run_id']}.json").write_text(json.dumps(doc))

    def doctor(self, day: str, *statuses: str) -> None:
        results = [{"name": f"check{i}", "status": s, "detail": "", "fix": ""} for i, s in enumerate(statuses or ["PASS"])]
        (self.a / "doctor" / f"doctor_paper_{day}.json").write_text(json.dumps({"ran_at": day, "results": results}))

    def nav(self, day: str, close: float | None = 1_000_000.0, flex: float | None = 1_000_000.0) -> None:
        if close is not None:
            self.close[day] = close
        if flex is not None:
            self.flex[day] = flex
        for name, rows in (("nav_paper.csv", self.close), ("flex_nav_paper.csv", self.flex)):
            (self.a / "api" / name).write_text("date,nav\n" + "".join(f"{d},{v}\n" for d, v in sorted(rows.items())))

    def good(self, day: str) -> None:
        self.run(day)
        self.doctor(day)
        self.nav(day)

    def evaluate(self, since: str = "2026-10-14", today: date = TODAY, **kw) -> dict[str, et.Session]:
        out = et.evaluate(since=date.fromisoformat(since), today=today, root=self.root, **kw)
        return {s.day.isoformat(): s for s in out}


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


def statuses(s: et.Session) -> dict[str, str]:
    return {k: c.status for k, c in s.checks.items()}


def test_a_clean_session_passes_all_four(tree) -> None:
    tree.good("2026-10-15")
    s = tree.evaluate()["2026-10-15"]
    assert s.status == et.PASS and statuses(s) == dict.fromkeys(et.CHECKS, et.PASS)
    assert s.checks["nav"].note == "close $1,000,000.00, statement $1,000,000.00 (+0.000%)"


def test_each_check_fails_on_its_own_evidence(tree) -> None:
    tree.run("2026-10-14", diffs=1)  # one share off is a break, however small
    tree.doctor("2026-10-14", "PASS", "WARN", "FAIL")
    tree.nav("2026-10-14", close=1_001_500.0)  # 0.15% from the statement
    tree.run("2026-10-15", ok=False, halted="EOD reconciliation mismatch on 2026-10-14")
    s = tree.evaluate()
    assert statuses(s["2026-10-14"]) == {"doctor": "FAIL", "run": "PASS", "reconcile": "FAIL", "nav": "FAIL"}
    assert s["2026-10-14"].checks["doctor"].note == "doctor failed: check2"
    assert s["2026-10-14"].checks["reconcile"].note == "the reconcile found 1 difference from IBKR"
    assert s["2026-10-15"].checks["run"].note == "halted: EOD reconciliation mismatch on 2026-10-14"
    assert statuses(s["2026-10-15"])["doctor"] == "FAIL"  # no report that day
    assert s["2026-10-15"].checks["nav"].note == "no NAV recorded at the close"


def test_a_dry_run_is_not_the_days_run(tree) -> None:
    tree.good("2026-10-15")
    tree.run("2026-10-15", "230000", ok=True, dry_run=True)  # later, by hand, but dry
    tree.run("2026-10-16", "081400", dry_run=True)
    s = tree.evaluate()
    assert s["2026-10-15"].status == et.PASS
    assert s["2026-10-16"].checks["run"].note == "no run"


def test_a_reconcile_that_did_not_check_fails(tree) -> None:
    doc = {"run_id": "20261015_193500", "ok": True,
           "steps": {"reconcile": {"ok": True, "skipped": True, "reason": "no expected book yet"}}}
    (tree.a / "runs" / "daily_run_20261015_193500.json").write_text(json.dumps(doc))
    note = tree.evaluate()["2026-10-15"].checks["reconcile"].note
    assert note == "the reconcile didn't check: no expected book yet"


def test_todays_and_recent_evidence_is_pending_and_the_streak_skips_it(tree) -> None:
    for day in ("2026-10-14", "2026-10-15", "2026-10-19"):
        tree.good(day)
    tree.good("2026-10-16")
    tree.run("2026-10-16", "193600", diffs=2)  # the day's last real run had breaks
    tree.run("2026-10-20")
    tree.doctor("2026-10-20")
    tree.nav("2026-10-20", flex=None)  # Flex publishes it tomorrow morning
    tree.doctor("2026-10-21")  # today, before the run
    s = tree.evaluate()
    assert s["2026-10-16"].status == et.FAIL
    assert s["2026-10-20"].status == et.PENDING and statuses(s["2026-10-20"])["nav"] == et.PENDING
    assert statuses(s["2026-10-21"]) == {"doctor": "PASS", "run": "PENDING", "reconcile": "PENDING", "nav": "PENDING"}
    assert et.streak(list(s.values())) == 1  # 10-19 alone: 10-16 broke it, 10-20 and 10-21 are still open
    # a statement still missing after FLEX_LAG_SESSIONS fails
    tree.nav("2026-10-15", flex=None)
    tree.flex.pop("2026-10-15")
    tree.nav("2026-10-14")
    assert tree.evaluate()["2026-10-15"].checks["nav"].note == "Flex has no NAV for that day"


def test_ten_in_a_row_pass(tree) -> None:
    days = [d.isoformat() for d in et.sessions_between(date(2026, 10, 6), date(2026, 10, 19), 0)]
    assert len(days) == 10
    for day in days:
        tree.good(day)
    results = list(tree.evaluate(since="2026-10-06", today=date(2026, 10, 19)).values())
    assert et.streak(results) == 10  # the last of them is today, with all its evidence in
    assert "Streak: 10 of 10. Passed." in et.render(results, "paper", et.NAV_TOLERANCE)


def test_main_json(tree, monkeypatch, capsys) -> None:
    from datetime import datetime

    tree.good("2026-10-20")
    monkeypatch.setattr(et, "PROJECT_ROOT", tree.root)
    monkeypatch.setattr(et.clock, "now_et", lambda: datetime(2026, 10, 21, 12, 0, tzinfo=et.clock.NYSE_TZ))
    assert et.main(["--json", "--since", "2026-10-19"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["needed"] == 10 and doc["nav_tolerance"] == pytest.approx(0.001)
    assert [s["day"] for s in doc["sessions"]] == ["2026-10-19", "2026-10-20", "2026-10-21"]
