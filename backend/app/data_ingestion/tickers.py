"""One place for the symbol a company trades under today (prices, broker orders).

Renamed tickers: same company, same shares; Yahoo and Alpaca keep the history under the new symbol. MMC -> MRSH and
BK -> BNY were found on 2026-09-28 when both old symbols stopped returning prices (SEC's company_tickers.json).
"""
from __future__ import annotations

RENAMED = {"FB": "META", "ANTM": "ELV", "RE": "EG", "PKI": "RVTY", "FLT": "CPAY", "WLTW": "WTW", "ABC": "COR",
           "CTL": "LUMN", "FISV": "FI", "PEAK": "DOC", "HCP": "DOC", "BLL": "BALL", "COG": "CTRA", "TMK": "GL",
           "CBS": "PARA", "VIAC": "PARA", "SYMC": "GEN", "NLOK": "GEN", "KORS": "CPRI", "HRS": "LHX",
           "ADS": "BFH", "DISCA": "WBD", "GPS": "GAP", "MMC": "MRSH", "BK": "BNY"}


def trading_symbol(ticker: object) -> str:
    """Today's symbol for price data and orders: renamed tickers mapped, class shares with a dash (BRK.B -> BRK-B)."""
    t = str(ticker)
    return RENAMED.get(t, t).replace(".", "-")
