"""Download split/dividend-adjusted daily closes for the walk-forward simulation.

    pip install yfinance   # only needed for this script, not by the app
    python scripts/fetch_prices.py --end 2026-09-12

Writes data/prices.csv (date, one column per ticker). SPY is the market
context series and is never itself a prediction target.
"""
from __future__ import annotations

import argparse
from pathlib import Path

TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "JNJ", "PG", "KO", "WMT", "UNH", "HD", "CAT", "BA", "DIS", "NFLX", "AMD", "INTC", "SPY"]


def main() -> None:
    import yfinance as yf

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", required=True)
    args = ap.parse_args()
    out = Path(__file__).resolve().parents[1] / "data" / "prices.csv"
    out.parent.mkdir(exist_ok=True)
    df = yf.download(TICKERS, start=args.start, end=args.end, auto_adjust=True, progress=False)["Close"]
    df = df.dropna()
    df.index = df.index.strftime("%Y-%m-%d")
    df.to_csv(out)
    print(f"wrote {out}: {df.shape[0]} days x {df.shape[1]} tickers, {df.index[0]}..{df.index[-1]}")


if __name__ == "__main__":
    main()
