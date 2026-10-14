"""Unit tests for ``src.broker.ibkr.doctor`` against a fake gateway."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.broker.ibkr import doctor, settings  # noqa: E402

READ_ONLY_MSG = (
    "Error validating request.-'bN' : cause - The API interface is currently in Read-Only mode."
)


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, *args):
        for h in self.handlers:
            h(*args)


class FakeIB:
    """The subset of ib_async.IB the doctor uses, with knobs for failure modes."""

    def __init__(self, **kw):
        self.accounts = kw.get("accounts", ["DU1234567"])
        self.nlv = kw.get("nlv", "1000000.00")
        self.positions_ = kw.get("positions", [])
        self.open_trades = kw.get("open_trades", [])
        self.unknown = set(kw.get("unknown", []))
        self.bars_age_days = kw.get("bars_age_days", 1)
        self.data_type = kw.get("data_type", 1)
        self.quote = kw.get("quote", 571.25)
        self.read_only = kw.get("read_only", False)
        self.preset_note = kw.get("preset_note", False)  # IBKR's 10349 note on an accepted order
        self.previewed = []  # the orders whatIfOrder was asked about
        self.clock_skew = kw.get("clock_skew", 0)
        self.connect_error = kw.get("connect_error")
        self.errorEvent = _Event()
        self.client = SimpleNamespace(serverVersion=lambda: 178)
        self.RequestTimeout = 0
        self._connected = False

    def connect(self, host, port, clientId, timeout):
        if self.connect_error:
            code, exc = self.connect_error
            if code:
                self.errorEvent.emit(-1, code, "client id is already in use", None)
            raise exc
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def managedAccounts(self):
        return list(self.accounts)

    def accountSummary(self, account=""):
        tags = {
            "NetLiquidation": self.nlv,
            "TotalCashValue": "250000",
            "GrossPositionValue": "750000",
            "BuyingPower": "4000000",
        }
        return [SimpleNamespace(tag=t, value=v, currency="USD") for t, v in tags.items()]

    def positions(self, account=""):
        return list(self.positions_)

    def reqAllOpenOrders(self):
        return list(self.open_trades)

    def fills(self):
        return []

    def reqCurrentTime(self):
        return datetime.now(timezone.utc) - timedelta(seconds=self.clock_skew)

    def qualifyContracts(self, *contracts):
        out = []
        for i, c in enumerate(contracts):
            if c.symbol in self.unknown:
                out.append(None)
            else:
                c.conId = 1000 + i
                out.append(c)
        return out

    def reqHistoricalData(self, contract, **kw):
        last = date.today() - timedelta(days=self.bars_age_days)
        return [SimpleNamespace(date=last - timedelta(days=1), close=570.0),
                SimpleNamespace(date=last, close=571.0)]

    def reqMarketDataType(self, kind):
        pass

    def reqMktData(self, contract, *args):
        return SimpleNamespace(last=self.quote, close=float("nan"), bid=float("nan"),
                               ask=float("nan"), marketDataType=self.data_type)

    def cancelMktData(self, contract):
        pass

    def sleep(self, seconds):
        pass

    def whatIfOrder(self, contract, order):
        self.previewed.append(order)
        if self.read_only:
            self.errorEvent.emit(7, 321, READ_ONLY_MSG, contract)
            raise TimeoutError()
        if self.preset_note:
            # ib_async ends the request on the note, so no OrderState comes back
            self.errorEvent.emit(7, 10349, "Order TIF was set to DAY based on order preset.", contract)
            raise TimeoutError()
        return SimpleNamespace(initMarginChange="57125.0", commission=1.0, warningText="")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("IBKR_HOST", "IBKR_PORT_PAPER", "IBKR_PORT_LIVE", "IBKR_ACCOUNT_PAPER",
                 "IBKR_ACCOUNT_LIVE", "IBKR_CLIENT_ID_BASE_PAPER", "IBKR_CLIENT_ID_BASE_LIVE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "_dotenv_loaded", True)
    monkeypatch.setattr(settings, "BROKER_CONFIG_PATH", Path("/nonexistent/broker.yaml"))
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: port == 4002)


def _run(fake=None, **kw):
    fake = fake or FakeIB()
    results = doctor.run_doctor("paper", symbols=["SPY", "TLT"], timeout=0.05,
                                ib_factory=lambda: fake, **kw)
    return {r.name: r for r in results}, fake


def test_healthy_paper_gateway_passes_everything():
    results, fake = _run()
    assert {r.status for r in results.values()} == {doctor.PASS}, results
    assert "NLV $1,000,000" in results["balances"].detail
    assert results["handshake"].detail.endswith("clientId 309")
    assert fake.RequestTimeout == 0.05
    assert not fake.isConnected()


def test_nothing_listening_stops_early_and_suggests_tws_port(monkeypatch):
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: port == 7497)
    results, _ = _run()
    assert results["socket"].status == doctor.FAIL
    assert "IBKR_PORT_PAPER=7497" in results["socket"].fix
    assert results["handshake"].status == doctor.SKIP
    assert results["quotes"].status == doctor.SKIP


def test_live_port_open_is_not_suggested_for_paper(monkeypatch):
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: port == 4001)
    results, _ = _run()
    assert results["socket"].status == doctor.FAIL
    assert "4001" not in results["socket"].fix


def test_client_id_in_use_is_explained():
    fake = FakeIB(connect_error=(326, TimeoutError()))
    results, _ = _run(fake)
    assert results["handshake"].status == doctor.FAIL
    assert "already connected" in results["handshake"].fix


def test_paper_profile_on_live_account_stops():
    results, fake = _run(FakeIB(accounts=["U7654321"]))
    assert results["account"].status == doctor.FAIL
    assert results["balances"].status == doctor.SKIP
    assert not fake.isConnected()


def test_unknown_symbol_fails_contracts_but_rest_continues():
    results, _ = _run(FakeIB(unknown={"TLT"}))
    assert results["contracts"].status == doctor.FAIL
    assert "TLT" in results["contracts"].detail
    assert results["history"].status == doctor.PASS


def test_delayed_market_data_warns_with_sharing_fix():
    results, _ = _run(FakeIB(data_type=3))
    assert results["quotes"].status == doctor.WARN
    assert "delayed" in results["quotes"].detail
    assert "share market data" in results["quotes"].fix


def test_no_quote_warns():
    results, _ = _run(FakeIB(quote=float("nan")))
    assert results["quotes"].status == doctor.WARN


def test_stale_bars_warn():
    results, _ = _run(FakeIB(bars_age_days=12))
    assert results["history"].status == doctor.WARN


def test_read_only_api_is_diagnosed():
    results, _ = _run(FakeIB(read_only=True))
    assert results["order preview"].status == doctor.FAIL
    assert "Read-Only API" in results["order preview"].fix


def test_ibkrs_preset_note_is_not_a_rejection():
    # 10349 accompanies an accepted order; the live gateway answered the preview with it
    results, fake = _run(FakeIB(preset_note=True))
    preview = results["order preview"]
    assert preview.status == doctor.PASS and "accepted (IBKR notes: 10349:" in preview.detail
    # and the preview sends the time in force the engine sends, so the note shouldn't arise
    assert {o.tif for o in fake.previewed} == {"DAY"}


def test_short_preview_skipped_when_long_reference():
    spy = SimpleNamespace(symbol="SPY", conId=1000)
    long_spy = SimpleNamespace(account="DU1234567", contract=spy, position=100.0, avgCost=500.0)
    results, _ = _run(FakeIB(positions=[long_spy]))
    assert results["short preview"].status == doctor.SKIP
    assert "1 positions (0 short)" in results["positions"].detail


def test_orders_from_other_client_ids_warn():
    trade = SimpleNamespace(order=SimpleNamespace(clientId=121))
    results, _ = _run(FakeIB(open_trades=[trade]))
    assert results["orders"].status == doctor.WARN
    assert "Master API client ID" in results["orders"].fix


def test_clock_skew():
    assert _run(FakeIB(clock_skew=10))[0]["clock"].status == doctor.WARN
    assert _run(FakeIB(clock_skew=120))[0]["clock"].status == doctor.FAIL


def test_live_profile_skips_order_preview_by_default(monkeypatch):
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: port == 4001)
    fake = FakeIB(accounts=["U7654321"])
    results = doctor.run_doctor("live", symbols=["SPY"], timeout=0.05, ib_factory=lambda: fake)
    names = {r.name: r.status for r in results}
    assert "order preview" not in names
    assert names["account"] == doctor.PASS


def test_main_exit_code_and_json(monkeypatch, capsys):
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: False)
    assert doctor.main(["--json"]) == 1
    out = capsys.readouterr().out
    assert '"status": "FAIL"' in out


def test_render_shows_fix_lines():
    results = [doctor.CheckResult("socket", doctor.FAIL, "nothing listening", "start gateway")]
    text = doctor.render(results, settings.resolve_endpoint("paper"))
    assert "→ start gateway" in text
    assert "1 failed" in text
