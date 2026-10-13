"""IBKR Flex: the walk back a year at a time to the account's first NAV, pacing, the fallback when
IBKR refuses a date range, incremental refreshes, and one-line errors. A fake Flex Web Service
serves NAV rows by fd/td on a fake clock, so nothing here sleeps or reaches IBKR."""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.parse
from datetime import date, timedelta
from itertools import pairwise

import pytest

from dv02_api import flex, nyse

END = date(2026, 10, 9)  # the last completed session the fetches run through
TOKEN = "1" * 20  # not a real token
QUERY = "123456"
GET_STATEMENT = "https://fake.example/GetStatement"


def ymd(d: date) -> str:
    return f"{d:%Y%m%d}"


def window(k: int) -> tuple[str, str]:
    """The k-th 365-day window back from END, as fd/td."""
    td = END - timedelta(days=365 * k)
    return ymd(td - timedelta(days=364)), ymd(td)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        assert s >= 0
        self.now += s


def _response(status: str, **fields: str) -> bytes:
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return f"<FlexStatementResponse><Status>{status}</Status>{inner}</FlexStatementResponse>".encode()


def _fail(code: str, message: str) -> bytes:
    return _response("Fail", ErrorCode=code, ErrorMessage=message)


class FakeFlex:
    """IBKR's Flex Web Service for one account opened on `opened`, with NAV through `last`.
    SendRequest hands out a reference; GetStatement serves the NAV rows between fd and td, or the
    query's own period (its last `own_days` sessions) without them. `fail` maps a SendRequest's
    number (1-based) to what it gets instead: an (ErrorCode, ErrorMessage), an exception, or raw
    bytes."""

    def __init__(self, clock: Clock, opened: date, own_days: int = 2, refuse_dates: bool = False,
                 generating: int = 1, fail: dict | None = None) -> None:  # fmt: skip
        self.clock, self.opened, self.last = clock, opened, END
        self.own_days, self.refuse_dates, self.generating = own_days, refuse_dates, generating
        self.fail = fail or {}
        self.sends: list[tuple[float, str | None, str | None]] = []
        self.refs: dict[str, list] = {}

    def ranges(self) -> list[tuple[str | None, str | None]]:
        return [(fd, td) for _, fd, td in self.sends]

    def __call__(self, url: str, timeout: float = 30) -> bytes:
        path, _, query = url.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        assert q["t"] == TOKEN and q["v"] == "3"
        if path.endswith("/SendRequest"):
            assert q["q"] == QUERY and ("fd" in q) == ("td" in q)
            self.sends.append((self.clock.now, q.get("fd"), q.get("td")))
            failure = self.fail.get(len(self.sends))
            if isinstance(failure, BaseException):
                raise failure
            if isinstance(failure, bytes):
                return failure
            if failure:
                return _fail(*failure)
            if self.refuse_dates and "fd" in q:
                return _fail("1020", "Invalid request or unable to validate request.")
            ref = f"R{len(self.sends)}"
            self.refs[ref] = [q.get("fd"), q.get("td"), self.generating]
            return _response("Success", ReferenceCode=ref, Url=GET_STATEMENT)
        assert path == GET_STATEMENT
        state = self.refs[q["q"]]
        if state[2] > 0:
            state[2] -= 1
            return _fail("1019", "Statement generation in progress. Please try again shortly.")
        fd, td = state[0], state[1]
        if fd:
            days = nyse.trading_days(max(_day(fd), self.opened), min(_day(td), self.last))
        else:
            days = [d for d in nyse.trading_days(self.last - timedelta(days=30), self.last) if d >= self.opened]
            days = days[-self.own_days :]
        rows = "".join(
            f'<EquitySummaryByReportDateInBase reportDate="{d:%Y%m%d}" total="{1_000_000 + i}" />'
            for i, d in enumerate(days)
        )
        return (
            '<FlexQueryResponse queryName="DV02 NAV" type="AF"><FlexStatements count="1"><FlexStatement>'
            f"<EquitySummaryInBase>{rows}</EquitySummaryInBase></FlexStatement></FlexStatements></FlexQueryResponse>"
        ).encode()


def _day(raw: str) -> date:
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))


@pytest.fixture()
def ibkr(engine_tree, monkeypatch: pytest.MonkeyPatch):
    """serve(opened, **kw) installs a FakeFlex for an account opened that day and returns it."""
    clock = Clock()
    monkeypatch.setattr(flex, "PACER", flex.Pacer(clock=clock.time, sleep=clock.sleep))
    monkeypatch.setattr(flex, "last_business_day", lambda today=None: END)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", TOKEN)
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", QUERY)

    def serve(opened: date, **kw) -> FakeFlex:
        fake = FakeFlex(clock, opened, **kw)
        monkeypatch.setattr(flex, "_get", fake)
        return fake

    return serve


def cached_days() -> list[date]:
    return [d for d, _ in flex.read_cache("paper")]


def marker() -> dict:
    return json.loads(flex.marker_path("paper").read_text())


# ── the walk ───────────────────────────────────────────────────────────


def test_an_account_opened_500_days_ago_loads_in_two_windows(ibkr) -> None:
    opened = END - timedelta(days=500)
    fake = ibkr(opened)
    out = flex.refresh("paper")
    assert fake.ranges() == [window(0), window(1)] == [("20251010", "20261009"), ("20241010", "20251009")]
    days = cached_days()
    assert days == nyse.trading_days(opened, END) and days[0] < END - timedelta(days=365)
    assert out == f"Flex NAV: {len(days)} days, {days[0]} to {END}"
    m = marker()
    assert m["first_nav"] == days[0].isoformat() and m["through"] == END.isoformat()
    assert m["complete"] and m["date_ranges"] and m["windows"] == 2


def test_an_account_opened_100_days_ago_stops_after_one_window(ibkr) -> None:
    opened = END - timedelta(days=100)
    fake = ibkr(opened)
    out = flex.refresh("paper")
    assert fake.ranges() == [window(0)]
    assert cached_days() == nyse.trading_days(opened, END)
    assert "under 30 days" not in out


def test_a_window_with_no_nav_ends_the_walk(ibkr) -> None:
    fd1 = _day(window(1)[0])
    opened = nyse.trading_days(fd1, fd1 + timedelta(days=7))[0]  # the second window is full
    fake = ibkr(opened)
    flex.refresh("paper")
    assert fake.ranges() == [window(0), window(1), window(2)]  # the third had nothing
    assert cached_days()[0] == opened and marker()["complete"]


def test_the_walk_stops_after_ten_windows(ibkr) -> None:
    fake = ibkr(date(2000, 1, 3))
    flex.refresh("paper")
    assert fake.ranges() == [window(k) for k in range(10)]
    assert cached_days()[0] == nyse.trading_days(_day(window(9)[0]), END)[0]


def test_a_new_account_or_a_foreign_token_is_called_out(ibkr) -> None:
    days = nyse.trading_days(END - timedelta(days=10), END)
    ibkr(days[0])
    out = flex.refresh("paper")
    assert out.startswith(f"Flex NAV: {len(days)} days, {days[0]} to {END}; under 30 days in all: ")
    assert "doesn't own it" in out


def test_an_older_window_that_fails_keeps_the_newer_rows_and_walks_again(ibkr) -> None:
    opened = END - timedelta(days=500)
    fake = ibkr(opened, fail={2: ("1009", "The server is under heavy load. Please try again shortly.")})
    out = flex.refresh("paper")
    assert fake.ranges() == [window(0), window(1)]
    assert out.endswith("; nothing before 2025-10-10 yet (1009: The server is under heavy load. Please try "
                        "again shortly.), the next refresh tries again")  # fmt: skip
    assert cached_days()[0] >= _day(window(0)[0]) and not marker()["complete"]
    fake.fail, fake.sends = {}, []
    flex.refresh("paper")  # not complete: the next refresh walks again, not just the last weeks
    assert fake.ranges() == [window(0), window(1)]
    assert cached_days() == nyse.trading_days(opened, END) and marker()["complete"]


@pytest.mark.parametrize("failure", [urllib.error.URLError("timed out"), TimeoutError("The read operation timed out")])
def test_an_older_window_that_times_out_keeps_the_newer_rows(ibkr, failure) -> None:
    fake = ibkr(END - timedelta(days=500), fail={2: failure})
    out = flex.refresh("paper")
    assert fake.ranges() == [window(0), window(1)] and "timed out), the next refresh tries again" in out
    assert not marker()["complete"]


def test_no_statement_before_the_account_opened_is_the_start(ibkr) -> None:
    fd1 = _day(window(1)[0])
    fake = ibkr(nyse.trading_days(fd1, fd1 + timedelta(days=7))[0], fail={3: ("1003", "Statement is not available.")})
    out = flex.refresh("paper")
    assert len(fake.sends) == 3 and ";" not in out and marker()["complete"]


# ── pacing ─────────────────────────────────────────────────────────────


def test_requests_never_exceed_one_a_second_or_ten_a_minute(ibkr) -> None:
    fake = ibkr(date(2000, 1, 3), generating=0)  # statements ready at once, so only pacing spaces them
    flex.refresh("paper")
    flex.refresh("paper", full=True)
    times = [t for t, _, _ in fake.sends]
    assert len(times) == 20
    assert all(b - a >= 1.2 for a, b in pairwise(times))
    assert all(times[i + 10] - times[i] >= 60 for i in range(len(times) - 10))
    assert times[-1] - times[0] >= 60  # it waited instead of failing


def test_the_pacer_is_shared_and_sleeps() -> None:
    clock = Clock()
    pacer = flex.Pacer(clock=clock.time, sleep=clock.sleep)
    sent = []
    for _ in range(25):
        pacer.wait()
        sent.append(clock.now)
    assert all(b - a >= 1.2 for a, b in pairwise(sent))
    assert all(sent[i + 10] - sent[i] >= 60 for i in range(len(sent) - 10))


def test_a_busy_token_waits_and_asks_again(ibkr) -> None:
    fake = ibkr(END - timedelta(days=100), fail={1: ("1018", "Too many requests have been made from this token.")})
    flex.refresh("paper")
    assert fake.ranges() == [window(0), window(0)] and fake.sends[1][0] - fake.sends[0][0] >= 30


# ── the query's own period, when IBKR refuses a date range ─────────────


def test_a_refused_date_range_falls_back_to_one_plain_request(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500), refuse_dates=True, own_days=2)
    out = flex.refresh("paper")
    assert fake.ranges() == [window(0), (None, None)]
    assert out.startswith("Flex NAV: 2 days, 2026-10-08 to 2026-10-09; IBKR refused a date range (1020: Invalid ")
    assert out.endswith("so this is only the query's own period: set its Period to Last 365 Calendar Days")
    assert "under 30 days" not in out and marker()["date_ranges"] is False
    fake.sends = []
    flex.refresh("paper")  # later refreshes go straight to the plain request
    assert fake.ranges() == [(None, None)]


def test_other_refusals_are_not_mistaken_for_a_refused_date_range(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500), fail={1: ("1015", "Token is invalid.")})
    with pytest.raises(flex.FlexError, match="1015: Token is invalid"):
        flex.refresh("paper")
    assert len(fake.sends) == 1 and not flex.marker_path("paper").exists()
    assert not flex.rejects_dates(flex.FlexError("Statement could not be generated at this time.", "1001"))
    assert not flex.rejects_dates(flex.FlexError("Invalid request or unable to validate request.", "1018"))
    assert flex.rejects_dates(flex.FlexError("Invalid date range.", "1099"))


# ── refreshes after the walk ───────────────────────────────────────────


def test_once_walked_a_refresh_is_one_request_for_the_last_weeks(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500))
    first = flex.refresh("paper")
    fake.sends = []
    assert flex.refresh("paper") == first
    assert fake.ranges() == [(ymd(END - timedelta(days=34)), ymd(END))]

    later = date(2026, 12, 11)  # two months on, the API was off: it asks from the last cached day
    fake.last, fake.sends = later, []
    out = flex.refresh("paper", end=later)
    assert fake.ranges() == [(ymd(END), ymd(later))]
    assert cached_days()[-1] == later and out.endswith(f"to {later}")


def test_a_year_away_walks_again(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500))
    flex.refresh("paper")
    later = END + timedelta(days=400)
    fake.last, fake.sends = later, []
    flex.refresh("paper", end=later)
    assert len(fake.sends) == 3 and fake.sends[0][1:] == (ymd(later - timedelta(days=364)), ymd(later))


def test_notes_split_cleanly_for_tradeit() -> None:
    """bin/tradeit prints each "; "-separated note on its own line, so a note has no "; " itself."""
    notes = [
        flex.fallback_note(flex.FlexError("Invalid request or unable to validate request.", "1020")),
        flex._not_yet(END, flex.FlexError("The server is under heavy load.", "1009")),
    ]
    assert all("; " not in n for n in notes)


def test_main_full_forces_a_walk(ibkr, capsys) -> None:
    fake = ibkr(END - timedelta(days=500))
    assert flex.main(["paper"]) == 0 and len(fake.sends) == 2  # no marker yet: the walk
    fake.sends = []
    assert flex.main(["paper"]) == 0 and len(fake.sends) == 1  # marker: the last weeks
    fake.sends = []
    assert flex.main(["paper", "--full"]) == 0 and fake.ranges() == [window(0), window(1)]
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 3 and len(set(lines)) == 1 and lines[0].startswith("Flex NAV: ") and err == ""


# ── one-line errors ────────────────────────────────────────────────────

TIMEOUT = "IBKR's Flex service didn't answer in time; try again in a minute"
NOT_XML = "IBKR's Flex service returned something that isn't XML; try again in a minute"


@pytest.mark.parametrize(
    "failure, said",
    [
        (("1015", "Token is invalid."), "IBKR Flex said no: 1015: Token is invalid."),
        (("1012", "Token has expired."), "IBKR Flex said no: 1012: Token has expired."),
        (TimeoutError("The read operation timed out"), TIMEOUT),
        (socket.timeout("timed out"), TIMEOUT),
        (urllib.error.URLError("nodename nor servname provided, or not known"),
         "Could not reach IBKR's Flex service: nodename nor servname provided, or not known"),
        (ConnectionResetError(), "Could not reach IBKR's Flex service: ConnectionResetError"),
        (http.client.RemoteDisconnected("Remote end closed connection without response"),
         "Could not reach IBKR's Flex service: Remote end closed connection without response"),
        (b"<html><body>Service Unavailable", NOT_XML),
    ],
)  # fmt: skip
def test_errors_are_one_line(ibkr, capsys, failure, said) -> None:
    ibkr(END - timedelta(days=500), fail={1: failure})
    assert flex.main(["paper"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == said + "\n"
    assert not flex.cache_path("paper").exists() and not flex.marker_path("paper").exists()


def test_a_query_without_the_nav_section_says_so(ibkr, capsys) -> None:
    ibkr(END + timedelta(days=30))  # no NAV rows in any window
    assert flex.main(["paper"]) == 1
    assert capsys.readouterr().err == (
        "IBKR Flex said no: statement has no NAV rows; add 'Net Asset Value (NAV) in Base' to the query\n"
    )


def test_without_credentials_it_says_how_to_configure(engine_tree, capsys) -> None:
    assert flex.main([]) == 0
    assert capsys.readouterr().out.startswith("Flex not configured")


def test_bad_arguments_print_usage(capsys) -> None:
    assert flex.main(["paper", "--everything"]) == 2
    assert capsys.readouterr().err == "usage: python -m dv02_api.flex [profile] [--full]\n"


# ── the request itself ─────────────────────────────────────────────────


def test_fd_and_td_go_together() -> None:
    with pytest.raises(ValueError):
        flex.fetch_statement(TOKEN, QUERY, fd=END)


def test_every_request_names_a_user_agent(monkeypatch) -> None:
    seen = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return b"<x/>"

    monkeypatch.setattr(flex.urllib.request, "urlopen", lambda req, timeout: seen.append(req) or Resp())
    assert flex._get("https://fake.example/SendRequest?t=1") == b"<x/>"
    assert seen[0].get_header("User-agent") == "dv02-api/1.0"


def test_the_last_completed_session() -> None:
    assert flex.last_business_day(date(2026, 10, 13)) == date(2026, 10, 12)  # Columbus Day: NYSE is open
    assert flex.last_business_day(date(2026, 10, 12)) == date(2026, 10, 9)
    assert flex.last_business_day(date(2026, 10, 11)) == date(2026, 10, 9)
