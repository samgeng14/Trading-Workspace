"""When a batch of orders executes: at today's close or at the next open.

The targets are built from the last completed close (generate_targets never
reads a session that hasn't closed) and price the book at the close. So orders
go as market-on-close (IBKR orderType MOC) while the closing auctions still
take them, and otherwise as market-on-open for the next session (a market order
with time in force OPG). Never a plain market order: outside regular hours one
waits for the open anyway, without saying so.

MOC entry cutoffs, before the close, so an early close moves them with it:
NYSE and NYSE Arca 10 minutes, Nasdaq and Cboe BZX 5. A batch goes MOC only
when it is sent MARGIN before the earliest cutoff among its symbols' primary
exchanges (src/broker/ibkr/contracts.py): by 15:45 ET on a normal day, 12:45
on an early close. The whole batch takes one decision, so its sells and buys
fill in the same auction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Literal

from src.broker.ibkr.contracts import primary_exchange
from src.common import clock

MOC_CUTOFF = {
    "NYSE": timedelta(minutes=10),
    "ARCA": timedelta(minutes=10),
    "NASDAQ": timedelta(minutes=5),
    "BATS": timedelta(minutes=5),
}
MARGIN = timedelta(minutes=5)
# The opening auctions take market-on-open orders until 09:28 ET. Between this
# and the open a batch goes MOC for the same day instead.
MOO_LAST_ENTRY = time(9, 25)


@dataclass(frozen=True)
class Execution:
    order_type: str  # IBKR orderType: "MOC", or "MKT" with tif "OPG" for market-on-open
    tif: str  # "DAY" or "OPG"
    auction: Literal["close", "open"]
    session: date  # the session whose close or open fills the orders
    fills_at: datetime  # that close or open, ET
    moc_deadline: datetime | None  # for MOC: the last time the batch may be sent

    @property
    def name(self) -> str:
        return "market-on-close" if self.auction == "close" else "market-on-open"

    def summary(self) -> str:
        """For example "market-on-close, filled at the Tue Oct 13 close"."""
        return f"{self.name}, filled at the {self.session:%a %b} {self.session.day} {self.auction}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_type": self.order_type,
            "tif": self.tif,
            "auction": self.auction,
            "session": self.session.isoformat(),
            "fills_at": self.fills_at.isoformat(),
            "moc_deadline": self.moc_deadline.isoformat() if self.moc_deadline else None,
            "summary": self.summary(),
        }


def moc_deadline(close_at: datetime, symbols: Iterable[str] = ()) -> datetime:
    """The last time a batch of ``symbols`` goes MOC for the session closing at
    ``close_at``. No symbols, or an exchange not in MOC_CUTOFF, takes NYSE's cutoff."""
    lead = max(
        (MOC_CUTOFF.get(primary_exchange(s), MOC_CUTOFF["NYSE"]) for s in symbols),
        default=MOC_CUTOFF["NYSE"],
    )
    return close_at - lead - MARGIN


def execution_for(symbols: Iterable[str] = (), at: datetime | None = None) -> Execution:
    """How a batch of ``symbols`` sent at ``at`` (default now) executes.

    On a trading day from MOO_LAST_ENTRY until the MOC deadline: MOC, at that
    day's close. Otherwise (before the open, after the deadline, or on a day
    the market is closed): market-on-open, at the next open.
    """
    at = at.astimezone(clock.NYSE_TZ) if at else clock.now_et()
    session = clock.market_session_for(at.date())
    if session is not None:
        deadline = moc_deadline(session.close_at, symbols)
        if datetime.combine(at.date(), MOO_LAST_ENTRY, tzinfo=clock.NYSE_TZ) <= at < deadline:
            return Execution("MOC", "DAY", "close", at.date(), session.close_at, deadline)
    opens = clock.next_market_open(at)
    return Execution("MKT", "OPG", "open", opens.date(), opens, None)
