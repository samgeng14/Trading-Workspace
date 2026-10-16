"""IBKR Flex: the walk back a year at a time to the account's first NAV, pacing, the two-step
protocol with its polling and retries, the fallback when IBKR refuses a date range, incremental
refreshes, reading the statement, the cache, and one-line errors that never carry the token. A
fake Flex Web Service serves NAV rows by fd/td on a fake clock, so nothing here sleeps or reaches
IBKR."""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.parse
from collections.abc import Iterable
from datetime import date, timedelta
from itertools import pairwise

import pytest

from dv02_api import flex, history, nyse

END = date(2026, 10, 9)  # the last completed session the fetches run through
TOKEN = "1" * 20  # not a real token
QUERY = "123456"
GET_STATEMENT = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
DEFAULT_GET = f"{flex.BASE}/GetStatement"


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


def statement(rows: Iterable[str], wrapped: bool = True) -> bytes:
    """A FlexQueryResponse with these NAV rows, inside a FlexStatement as IBKR sends them or bare."""
    inner = "".join(rows)
    if wrapped:
        head = '<FlexStatements count="1"><FlexStatement><EquitySummaryInBase>'
        inner = f"{head}{inner}</EquitySummaryInBase></FlexStatement></FlexStatements>"
    return f'<FlexQueryResponse queryName="DV02 NAV" type="AF">{inner}</FlexQueryResponse>'.encode()


def _serve(failure: object) -> bytes | None:
    """What a request gets instead of the real thing: an exception raised, raw bytes, or an
    (ErrorCode, ErrorMessage) as IBKR's Fail reply. None: the real thing."""
    if failure is None:
        return None
    if isinstance(failure, BaseException):
        raise failure
    if isinstance(failure, bytes):
        return failure
    return _fail(*failure)  # type: ignore[misc]


class FakeFlex:
    """IBKR's Flex Web Service for one account opened on `opened`, with NAV through `last`: a
    million plus ten dollars a day since it opened, so a date's NAV is the same whichever request
    serves it, unless `restated` says otherwise. SendRequest hands out a reference; GetStatement
    says "in progress" `generating` times, then serves the NAV rows between fd and td, or the
    query's own period (its last `own_days` sessions) without them. `fail` maps a SendRequest's
    number (1-based) to what it gets instead: an (ErrorCode, ErrorMessage), an exception, or raw
    bytes; `get_fail` does the same for GetStatement calls."""

    def __init__(self, clock: Clock, opened: date, own_days: int = 2, refuse_dates: bool = False,
                 generating: int = 1, fail: dict | None = None, get_fail: dict | None = None,
                 statement_url: str = GET_STATEMENT) -> None:  # fmt: skip
        self.clock, self.opened, self.last = clock, opened, END
        self.own_days, self.refuse_dates, self.generating = own_days, refuse_dates, generating
        self.fail, self.get_fail, self.statement_url = fail or {}, get_fail or {}, statement_url
        self.restated: dict[date, float] = {}
        self.sends: list[tuple[float, str | None, str | None]] = []
        self.gets: list[tuple[float, str, str]] = []  # when, where, which reference
        self.refs: dict[str, list] = {}

    def nav(self, d: date) -> float:
        return self.restated.get(d, 1_000_000.0 + 10 * (d - self.opened).days)

    def ranges(self) -> list[tuple[str | None, str | None]]:
        return [(fd, td) for _, fd, td in self.sends]

    def __call__(self, url: str, timeout: float = 30) -> bytes:
        path, _, query = url.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        assert q["t"] == TOKEN and q["v"] == "3"
        if path.endswith("/SendRequest"):
            assert q["q"] == QUERY and ("fd" in q) == ("td" in q)
            self.sends.append((self.clock.now, q.get("fd"), q.get("td")))
            served = _serve(self.fail.get(len(self.sends)))
            if served is not None:
                return served
            if self.refuse_dates and "fd" in q:
                return _fail("1020", "Invalid request or unable to validate request.")
            ref = f"R{len(self.sends)}"
            self.refs[ref] = [q.get("fd"), q.get("td"), self.generating]
            return _response("Success", ReferenceCode=ref, Url=self.statement_url)
        assert path in (GET_STATEMENT, DEFAULT_GET), path
        self.gets.append((self.clock.now, path, q["q"]))
        served = _serve(self.get_fail.get(len(self.gets)))
        if served is not None:
            return served
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
        return statement(
            f'<EquitySummaryByReportDateInBase reportDate="{d:%Y%m%d}" total="{self.nav(d)}" />' for d in days
        )


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


@pytest.mark.parametrize("days_in", [3, 12])
def test_the_opening_near_a_window_start_decides_whether_to_look_further(ibkr, days_in) -> None:
    """A first NAV within a week of a window's start could be a full window, so one more is asked
    (and comes back empty); later than that, the window holds the opening and the walk stops."""
    fd1 = _day(window(1)[0])
    opened = nyse.trading_days(fd1 + timedelta(days=days_in), END)[0]
    fake = ibkr(opened)
    flex.refresh("paper")
    assert len(fake.sends) == (3 if days_in <= 7 else 2)
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
    fake = ibkr(END - timedelta(days=500), fail={2: failure, 3: failure})  # the window's request and its retry
    out = flex.refresh("paper")
    assert fake.ranges() == [window(0), window(1), window(1)]
    assert "(timed out), the next refresh tries again" in out and not marker()["complete"]


def test_no_statement_before_the_account_opened_is_the_start(ibkr) -> None:
    fd1 = _day(window(1)[0])
    fake = ibkr(nyse.trading_days(fd1, fd1 + timedelta(days=7))[0], fail={3: ("1003", "Statement is not available.")})
    out = flex.refresh("paper")
    assert len(fake.sends) == 3 and ";" not in out and marker()["complete"]


@pytest.mark.parametrize(
    "message, complete", [("Invalid date range.", True), ("Invalid request or unable to validate request.", False)]
)
def test_a_refused_range_on_an_older_window(ibkr, message, complete) -> None:
    """A refusal that names the dates is IBKR's earliest; one that doesn't is tried again next time."""
    fd1 = _day(window(1)[0])
    fake = ibkr(nyse.trading_days(fd1, fd1 + timedelta(days=7))[0], fail={3: ("1020", message)})
    out = flex.refresh("paper")
    assert len(fake.sends) == 3 and marker()["complete"] is complete
    if complete:
        assert ";" not in out
    else:
        assert out.endswith(f"; nothing before {fd1} yet (1020: {message}), the next refresh tries again")


def test_a_restated_nav_replaces_the_cached_one_and_dates_stay_unique(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500))
    flex.refresh("paper")
    before = dict(flex.read_cache("paper"))
    fake.restated[END] = 999_999.5
    flex.refresh("paper")  # the last weeks again: five weeks of it overlap the cache
    after = flex.read_cache("paper")
    assert dict(after) == {**before, END: 999_999.5}
    assert [d for d, _ in after] == sorted({d for d, _ in after})
    lines = flex.cache_path("paper").read_text().splitlines()
    assert lines[0] == "date,nav" and lines[1:] == sorted(lines[1:]) and len(lines) == len(after) + 1


def test_full_and_the_periodic_refresh_give_the_same_rows(ibkr) -> None:
    opened = END - timedelta(days=500)
    fake = ibkr(opened)
    flex.refresh("paper")  # the walk
    later = date(2026, 11, 20)
    fake.last = later
    flex.refresh("paper", end=later)  # the last weeks
    periodic = flex.read_cache("paper")
    flex.refresh("paper", full=True, end=later)  # the walk again
    assert flex.read_cache("paper") == periodic == [(d, fake.nav(d)) for d in nyse.trading_days(opened, later)]


# ── the two steps: SendRequest, then GetStatement until it's generated ──


def test_a_statement_in_progress_is_polled_with_a_pause(ibkr) -> None:
    fake = ibkr(END - timedelta(days=100), generating=3)
    flex.refresh("paper")
    polls = [t for t, _, ref in fake.gets if ref == "R1"]
    assert len(polls) == 4 and all(b - a >= flex.POLL_PAUSE for a, b in pairwise(polls))
    assert cached_days() == nyse.trading_days(END - timedelta(days=100), END)


def test_a_statement_that_never_finishes_is_a_one_line_reason(ibkr, capsys) -> None:
    fake = ibkr(END - timedelta(days=100), generating=10**6)
    t0 = fake.clock.now
    assert flex.main(["paper"]) == 1
    assert capsys.readouterr().err == (
        "IBKR Flex said no: 1019: Statement generation in progress. Please try again shortly.\n"
    )
    assert fake.clock.now - t0 >= 90 and not flex.cache_path("paper").exists()


@pytest.mark.parametrize(
    "reply, said",
    [
        (b"<FlexStatementResponse><Status>Fail</Status></FlexStatementResponse>",
         "unexpected reply from IBKR (<FlexStatementResponse>, status 'Fail')"),
        (b"<FlexStatementResponse><Status>Success</Status></FlexStatementResponse>",
         "IBKR accepted the request but gave no reference code"),
    ],
)  # fmt: skip
def test_a_reply_with_no_error_in_it_is_still_a_reason(ibkr, capsys, reply, said) -> None:
    ibkr(END - timedelta(days=100), fail={1: reply})
    assert flex.main(["paper"]) == 1
    assert capsys.readouterr().err == f"IBKR Flex said no: {said}\n"


@pytest.mark.parametrize(
    "advertised, used",
    [
        (GET_STATEMENT, GET_STATEMENT),
        ("https://evil.example/GetStatement", DEFAULT_GET),
        ("http://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement", DEFAULT_GET),
        ("https://interactivebrokers.com.evil.example/GetStatement", DEFAULT_GET),
        ("gdcdyn.interactivebrokers.com/GetStatement", DEFAULT_GET),  # urllib would name this one, token and all
        ("https://gdcdyn.interactivebrokers.com/GetStatement?x=1", DEFAULT_GET),
        ("", DEFAULT_GET),
    ],
)
def test_the_token_goes_only_to_ibkr_over_https(ibkr, advertised, used) -> None:
    """GetStatement is fetched where IBKR's reply says, as the protocol goes, unless that isn't one
    of IBKR's hosts over https; then at the service's own GetStatement."""
    fake = ibkr(END - timedelta(days=100), statement_url=advertised)
    flex.refresh("paper")
    assert {path for _, path, _ in fake.gets} == {used}


def test_a_dropped_connection_is_tried_once_more(ibkr) -> None:
    dropped = http.client.RemoteDisconnected("Remote end closed connection without response")
    fake = ibkr(END - timedelta(days=100), fail={1: ConnectionResetError()}, get_fail={1: dropped})
    flex.refresh("paper")
    assert fake.ranges() == [window(0), window(0)] and fake.sends[1][0] - fake.sends[0][0] >= flex.RETRY_PAUSE
    assert len(fake.gets) == 3 and fake.gets[1][0] - fake.gets[0][0] >= flex.POLL_PAUSE  # dropped, generating, served
    assert cached_days() == nyse.trading_days(END - timedelta(days=100), END)


def test_a_connection_that_keeps_dropping_while_polling_gives_up_at_the_deadline(ibkr, capsys) -> None:
    fake = ibkr(END - timedelta(days=100), get_fail={n: ConnectionResetError() for n in range(1, 200)})
    t0 = fake.clock.now
    assert flex.main(["paper"]) == 1
    assert capsys.readouterr().err == "Could not reach IBKR's Flex service: ConnectionResetError\n"
    assert 90 <= fake.clock.now - t0 < 200 and len(fake.gets) < 100


# ── the token and the query id appear nowhere ──────────────────────────


def test_the_token_and_the_query_id_appear_nowhere(ibkr, capsys) -> None:
    """Not in the one-line result, the cache or the marker, and not in an error: neither one IBKR
    builds around them nor one urllib raises with the URL in hand."""
    fake = ibkr(END - timedelta(days=100))
    assert flex.main(["paper"]) == 0
    out = capsys.readouterr()
    texts = [out.out, out.err, flex.cache_path("paper").read_text(), flex.marker_path("paper").read_text()]

    fake.fail, fake.sends = {1: ("1015", f"Token {TOKEN} for query {QUERY} is invalid.")}, []
    with pytest.raises(flex.FlexError) as said:
        flex.refresh("paper", full=True)
    assert str(said.value) == "1015: Token … for query … is invalid."
    texts.append(str(said.value))

    url = f"{flex.BASE}/SendRequest?t={TOKEN}&q={QUERY}&v=3"
    http_error = urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
    fake.fail, fake.sends = {1: http_error, 2: http_error}, []
    with pytest.raises(urllib.error.HTTPError) as raised:
        flex.refresh("paper", full=True)  # the API's refresh thread logs str(exc)
    texts += [str(raised.value), repr(raised.value), flex._why(raised.value)]
    assert all(TOKEN not in t and QUERY not in t for t in texts)


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


def test_a_marker_that_cannot_be_read_means_a_walk(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500))
    flex.refresh("paper")
    fake.sends = []
    flex.marker_path("paper").write_text(json.dumps({**marker(), "first_nav": "soon"}))
    flex.refresh("paper")
    assert fake.ranges() == [window(0), window(1)]


def test_notes_split_cleanly_for_tradeit() -> None:
    """bin/tradeit prints each "; "-separated note on its own line, so a note has no "; " itself,
    whatever IBKR's message had."""
    notes = [
        flex.fallback_note(flex.FlexError("Invalid request or unable to validate request.", "1020")),
        flex.fallback_note(flex.FlexError("Bad period; name fd and td.", "1020")),
        flex._not_yet(END, flex.FlexError("The server is under heavy load.", "1009")),
        flex._not_yet(END, flex.FlexError("Heavy load; try later.", "1009")),
    ]
    assert all("; " not in n for n in notes)
    assert notes[3] == "nothing before 2026-10-10 yet (1009: Heavy load, try later.), the next refresh tries again"


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


# ── reading the statement ──────────────────────────────────────────────

ROW = '<EquitySummaryByReportDateInBase accountId="DU1234567" reportDate="{d}" total="{v}" />'


def rows(*pairs: tuple[str, str], wrapped: bool = True) -> bytes:
    return statement((ROW.format(d=d, v=v) for d, v in pairs), wrapped=wrapped)


def test_nav_rows_are_read_wherever_they_sit() -> None:
    pairs = (("20261008", "1000.5"), ("20261009", "1001"))
    want = [(date(2026, 10, 8), 1000.5), (date(2026, 10, 9), 1001.0)]
    assert flex.parse_nav(rows(*pairs)) == flex.parse_nav(rows(*pairs, wrapped=False)) == want


def test_rows_without_a_nav_yet_and_non_trading_days_are_left_out() -> None:
    body = rows(("20261009", "1001"), ("20261012", ""), ("20261013", "NaN"), ("20261014", "n/a"),
                ("20261010", "1002"), ("20260907", "1003"), ("20261009", "1001.25"))  # fmt: skip
    # the 10th is a Saturday and 7 September Labor Day; a date repeated keeps its last row
    assert flex.parse_nav(body) == [(date(2026, 10, 9), 1001.25)]
    assert flex.parse_nav(rows(("20261009", ""))) == []  # a period with no NAV yet is not an error


@pytest.mark.parametrize("raw", ["20261009", "2026-10-09", "2026/10/09", "09-Oct-2026"])
def test_the_date_formats_that_cannot_be_misread_are_read(raw) -> None:
    assert flex.parse_nav(rows((raw, "1"))) == [(date(2026, 10, 9), 1.0)]


def test_a_date_format_that_could_be_misread_says_which_to_set() -> None:
    with pytest.raises(flex.FlexError, match=r"^can't read the report dates \('10/09/2026'\); set the query's Date"):
        flex.parse_nav(rows(("10/09/2026", "1")))


def test_rows_missing_the_fields_say_what_to_select() -> None:
    no_total = statement(['<EquitySummaryByReportDateInBase accountId="DU1234567" reportDate="20261009" />'])
    with pytest.raises(flex.FlexError, match=r"select all fields of 'Net Asset Value \(NAV\) in Base'"):
        flex.parse_nav(no_total)
    renamed = statement(['<EquitySummaryByReportDateInBase date="20261009" total="1" />'])
    with pytest.raises(flex.FlexError, match="lack the Report Date or Total field"):
        flex.parse_nav(renamed)


def test_a_statement_over_two_accounts_is_refused() -> None:
    other = ROW.format(d="20261009", v="2").replace("DU1234567", "DU7654321")
    with pytest.raises(flex.FlexError, match="covers 2 accounts; set the query to the paper account only"):
        flex.parse_nav(statement([ROW.format(d="20261009", v="1"), other]))


@pytest.mark.parametrize(
    "body, said",
    [
        (b'"ClientAccountID","AccountAlias","Model"\n"DU1234567","",""\n', flex.NOT_XML_STATEMENT),
        (b"<html><body>Service Unavailable</body></html>", flex.NOT_FLEX),  # a page that parses as XML
        (b"<html><body>Service Unavailable", flex.NOT_FLEX),  # one that doesn't
        (b"", flex.NOT_FLEX),
        (b"<FlexQueryResponse><FlexStatements><FlexStatement><EquitySummaryInBase>", flex.NOT_FLEX),  # cut off
    ],
)
def test_a_statement_that_is_not_a_flex_reply_says_so(ibkr, capsys, body, said) -> None:
    ibkr(END - timedelta(days=100), get_fail={2: body})  # the first poll says "in progress"
    assert flex.main(["paper"]) == 1
    assert capsys.readouterr().err == said + "\n"
    assert not flex.cache_path("paper").exists()


def test_a_page_instead_of_an_older_statement_is_noted_in_plain_words(ibkr) -> None:
    fake = ibkr(END - timedelta(days=500), get_fail={4: b"<html>maintenance</html>"})
    out = flex.refresh("paper")
    why = flex.NOT_FLEX.replace("; ", ", ")
    assert out.endswith(f"; nothing before {_day(window(0)[0])} yet ({why}), the next refresh tries again")
    assert fake.ranges() == [window(0), window(1)] and not marker()["complete"]


# ── the cache ──────────────────────────────────────────────────────────


def test_a_corrupt_cache_keeps_its_good_rows_and_a_refresh_adds_to_them(ibkr) -> None:
    fake = ibkr(END - timedelta(days=100))
    flex.cache_path("paper").write_bytes(
        b"date,nav\n2026-10-01,1.00\n2020-01-03,900000.00\n2026-10-01\nnot a date,5\n2026-10-01,2.00\n"
        b"\x00\xff\xfe garbage\n2026-10-02,nan\n2020-01-02,800000.00\n"
    )
    good = [(date(2020, 1, 2), 800000.0), (date(2020, 1, 3), 900000.0), (date(2026, 10, 1), 2.0)]
    assert flex.read_cache("paper") == good  # sorted, one per date, the last value for a repeated date
    flex.refresh("paper")
    cache = dict(flex.read_cache("paper"))
    assert cache[date(2020, 1, 2)] == 800000.0 and cache[date(2020, 1, 3)] == 900000.0  # before Flex: kept
    assert cache[date(2026, 10, 1)] == fake.nav(date(2026, 10, 1))  # Flex's own value
    assert "garbage" not in flex.cache_path("paper").read_text()


def test_a_cache_that_cannot_be_read_at_all_is_empty_not_fatal(engine_tree) -> None:
    flex.cache_path("paper").write_bytes(b"\xff\xfe\x00\x00\x00\x00")
    assert flex.read_cache("paper") == []
    assert history.nav_history("paper").navs  # the dashboard's history still answers from the other sources


def test_cache_writes_are_atomic_and_leave_no_temp_file(engine_tree, monkeypatch) -> None:
    flex._write_cache("paper", {date(2026, 10, 9): 1.0})
    folder = flex.cache_path("paper").parent

    def refuse(src, dst):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(flex.os, "replace", refuse)
        with pytest.raises(OSError):
            flex._write_cache("paper", {date(2026, 10, 9): 2.0})
    assert flex.read_cache("paper") == [(date(2026, 10, 9), 1.0)]
    assert [p.name for p in folder.iterdir() if "flex" in p.name] == ["flex_nav_paper.csv"]
    flex._write_cache("paper", {date(2026, 10, 9): 2.0, date(2026, 10, 8): float("nan")})
    assert flex.cache_path("paper").read_text() == "date,nav\n2026-10-09,2.00\n"


def test_a_cache_that_cannot_be_written_is_said_so_not_blamed_on_ibkr(ibkr, capsys, monkeypatch) -> None:
    ibkr(END - timedelta(days=100))

    def refuse(profile: str, navs: dict) -> None:
        raise PermissionError(13, "Permission denied", "flex_nav_paper.csv")

    monkeypatch.setattr(flex, "_write_cache", refuse)
    assert flex.main(["paper"]) == 1
    said = "Could not write the Flex cache: [Errno 13] Permission denied: 'flex_nav_paper.csv'"
    assert capsys.readouterr().err == said + "\n"


# ── one-line errors ────────────────────────────────────────────────────

TIMEOUT = "IBKR's Flex service didn't answer in time; try again in a minute"


@pytest.mark.parametrize(
    "failure, said",
    [
        (("1015", "Token is invalid."), "IBKR Flex said no: 1015: Token is invalid."),
        (("1012", "Token has expired."), "IBKR Flex said no: 1012: Token has expired."),
        (TimeoutError("The read operation timed out"), TIMEOUT),
        (socket.timeout("timed out"), TIMEOUT),
        (urllib.error.URLError(TimeoutError("_ssl.c:980: The handshake operation timed out")), TIMEOUT),
        (urllib.error.URLError("nodename nor servname provided, or not known"),
         "Could not reach IBKR's Flex service: nodename nor servname provided, or not known"),
        (ConnectionResetError(), "Could not reach IBKR's Flex service: ConnectionResetError"),
        (http.client.RemoteDisconnected("Remote end closed connection without response"),
         "Could not reach IBKR's Flex service: Remote end closed connection without response"),
        (urllib.error.HTTPError(f"{flex.BASE}/SendRequest", 503, "Service Unavailable", {}, None),
         "IBKR's Flex service answered HTTP 503 Service Unavailable; try again in a minute"),
        (b"<html><body>Service Unavailable</body></html>", flex.NOT_FLEX),
    ],
)  # fmt: skip
def test_errors_are_one_line(ibkr, capsys, failure, said) -> None:
    ibkr(END - timedelta(days=500), fail={1: failure, 2: failure})  # a dropped connection is tried once more
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


def test_fd_and_td_go_together_and_in_order() -> None:
    with pytest.raises(ValueError):
        flex.fetch_statement(TOKEN, QUERY, fd=END)
    with pytest.raises(ValueError):
        flex.fetch_statement(TOKEN, QUERY, fd=END, td=END - timedelta(days=1))


def test_every_request_names_a_user_agent_and_a_timeout(monkeypatch) -> None:
    seen = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return b"<x/>"

    monkeypatch.setattr(flex.urllib.request, "urlopen", lambda req, timeout: seen.append((req, timeout)) or Resp())
    assert flex._get("https://fake.example/SendRequest?t=1") == b"<x/>"
    req, timeout = seen[0]
    assert req.get_header("User-agent") == "dv02-api/1.0" and timeout == flex.TIMEOUT_S > 0


def test_the_last_completed_session() -> None:
    assert flex.last_business_day(date(2026, 10, 13)) == date(2026, 10, 12)  # Columbus Day: NYSE is open
    assert flex.last_business_day(date(2026, 10, 12)) == date(2026, 10, 9)
    assert flex.last_business_day(date(2026, 10, 11)) == date(2026, 10, 9)
