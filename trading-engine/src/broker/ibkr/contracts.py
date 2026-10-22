from __future__ import annotations

from typing import Iterable

try:
    from ib_async import IB, Stock
except ImportError:  # pragma: no cover
    from ib_insync import IB, Stock  # type: ignore


# Each ETF's primary listing exchange (IBKR's names; BATS is Cboe BZX). Unknown
# symbols are taken to be on NYSE Arca. src/broker/ibkr/auction.py reads it for
# the closing-auction cutoffs.
PRIMARY_EXCHANGE = {
    "SPY": "ARCA",
    "QQQ": "NASDAQ",
    "IBIT": "NASDAQ",
    "ETHA": "NASDAQ",
    "TQQQ": "NASDAQ",
    "GLD": "ARCA",
    "SLV": "ARCA",
    "UNG": "ARCA",
    "USO": "ARCA",
    "BIL": "ARCA",
    "VEA": "ARCA",
    "VWO": "ARCA",
    "VNQ": "ARCA",
    "VPU": "ARCA",
    "VFH": "ARCA",
    "VHT": "ARCA",
    "VIS": "ARCA",
    "INDA": "ARCA",
    "VIXY": "BATS",
    "TLT": "NASDAQ",
    "SHY": "NASDAQ",
    "IEI": "NASDAQ",
}


def primary_exchange(symbol: str) -> str:
    return PRIMARY_EXCHANGE.get(symbol.upper().strip(), "ARCA")


def _candidates(symbol: str) -> Iterable[Stock]:
    symbol = symbol.upper().strip()
    primary = primary_exchange(symbol)

    # Try several common IBKR contract forms.
    yield Stock(symbol=symbol, exchange="SMART", currency="USD", primaryExchange=primary)
    yield Stock(symbol=symbol, exchange=primary, currency="USD")
    yield Stock(symbol=symbol, exchange="SMART", currency="USD")
    yield Stock(symbol=symbol, exchange="ARCA", currency="USD")
    yield Stock(symbol=symbol, exchange="BATS", currency="USD")
    yield Stock(symbol=symbol, exchange="NASDAQ", currency="USD")
    yield Stock(symbol=symbol, exchange="ISLAND", currency="USD")


def qualify_us_stock_contract(ib: IB, symbol: str) -> Stock:
    last_error: str | None = None

    for contract in _candidates(symbol):
        try:
            qualified = ib.qualifyContracts(contract)
            if qualified and qualified[0] is not None:
                return qualified[0]
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)

    raise ValueError(f"Could not qualify contract for {symbol}. Last error: {last_error}")