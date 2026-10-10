"""What each instrument is: name, asset class, and the rough sensitivities the
risk scenarios use. Symbols outside this table still work; they show up as
equities with beta 1 until added here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    asset_class: str  # contract AssetClass
    duration: float = 0.0  # years, for the rates scenario
    leverage: float = 1.0


_ETFS = [
    Instrument("SPY", "SPDR S&P 500", "equity"),
    Instrument("QQQ", "Invesco QQQ (Nasdaq-100)", "equity"),
    Instrument("TQQQ", "ProShares UltraPro QQQ", "equity", leverage=3.0),
    Instrument("VEA", "Vanguard Developed Markets", "equity"),
    Instrument("VWO", "Vanguard Emerging Markets", "equity"),
    Instrument("INDA", "iShares MSCI India", "equity"),
    Instrument("VFH", "Vanguard Financials", "equity"),
    Instrument("VHT", "Vanguard Health Care", "equity"),
    Instrument("VIS", "Vanguard Industrials", "equity"),
    Instrument("VNQ", "Vanguard Real Estate", "equity", duration=4.0),
    Instrument("VPU", "Vanguard Utilities", "equity", duration=3.0),
    Instrument("TLT", "iShares 20+ Year Treasury", "fixed_income", duration=16.5),
    Instrument("IEI", "iShares 3-7 Year Treasury", "fixed_income", duration=4.4),
    Instrument("SHY", "iShares 1-3 Year Treasury", "fixed_income", duration=1.9),
    Instrument("BIL", "SPDR 1-3 Month T-Bill", "cash", duration=0.1),
    Instrument("GLD", "SPDR Gold Shares", "commodity"),
    Instrument("SLV", "iShares Silver Trust", "commodity"),
    Instrument("USO", "United States Oil Fund", "commodity"),
    Instrument("UNG", "United States Natural Gas", "commodity"),
    Instrument("IBIT", "iShares Bitcoin Trust", "crypto"),
    Instrument("ETHA", "iShares Ethereum Trust", "crypto"),
    Instrument("VIXY", "ProShares VIX Short-Term Futures", "volatility"),
    # benchmarks and references
    Instrument("IWM", "iShares Russell 2000", "equity"),
    Instrument("ACWI", "iShares MSCI ACWI", "equity"),
    Instrument("AGG", "iShares Core US Aggregate Bond", "fixed_income", duration=6.0),
]

BY_SYMBOL = {i.symbol: i for i in _ETFS}

# Futures roots the book may hold, by root symbol.
FUTURES = {
    "MES": Instrument("MES", "Micro E-mini S&P 500", "index_future"),
    "ES": Instrument("ES", "E-mini S&P 500", "index_future"),
    "MNQ": Instrument("MNQ", "Micro E-mini Nasdaq-100", "index_future"),
    "NQ": Instrument("NQ", "E-mini Nasdaq-100", "index_future"),
    "ZN": Instrument("ZN", "10-Year T-Note", "fixed_income", duration=6.5),
    "ZB": Instrument("ZB", "US Treasury Bond", "fixed_income", duration=15.0),
}


def lookup(symbol: str, sec_type: str = "STK") -> Instrument:
    """``symbol`` is the root for futures (IBKR's contract.symbol, e.g. MES)."""
    if sec_type == "FUT":
        return FUTURES.get(symbol, Instrument(symbol, symbol, "index_future"))
    return BY_SYMBOL.get(symbol, Instrument(symbol, symbol, "equity"))


BENCHMARKS = [
    {"id": "SPY", "label": "S&P 500", "description": "SPDR S&P 500 ETF, total return"},
    {"id": "QQQ", "label": "Nasdaq-100", "description": "Invesco QQQ, total return"},
    {"id": "IWM", "label": "Russell 2000", "description": "iShares Russell 2000, total return"},
    {"id": "ACWI", "label": "MSCI ACWI", "description": "iShares MSCI ACWI, total return"},
    {"id": "6040", "label": "60/40", "description": "60% SPY / 40% AGG, rebalanced daily"},
    {"id": "AGG", "label": "US Agg bonds", "description": "iShares Core US Aggregate Bond"},
    {"id": "BIL", "label": "T-bills", "description": "SPDR 1-3 Month T-Bill"},
]

# Ticker strip: (symbol, label, kind, contract spec). Index quotes need IBKR's
# free delayed index data; anything that doesn't answer is left out.
STRIP = [
    ("SPX", "S&P 500", "index", {"secType": "IND", "exchange": "CBOE"}),
    ("NDX", "Nasdaq-100", "index", {"secType": "IND", "exchange": "NASDAQ"}),
    ("RUT", "Russell 2000", "index", {"secType": "IND", "exchange": "RUSSELL"}),
    ("VIX", "VIX", "index", {"secType": "IND", "exchange": "CBOE"}),
    ("TNX", "US 10Y", "yield", {"secType": "IND", "exchange": "CBOE"}),
    ("GLD", "Gold (GLD)", "price", {"secType": "STK", "exchange": "SMART"}),
    ("USO", "Oil (USO)", "price", {"secType": "STK", "exchange": "SMART"}),
    ("IBIT", "Bitcoin (IBIT)", "price", {"secType": "STK", "exchange": "SMART"}),
    ("EUR", "EUR/USD", "fx", {"secType": "CASH", "exchange": "IDEALPRO", "currency": "USD"}),
]
