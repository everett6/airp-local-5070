"""Fetch daily OHLCV bars for the tickers of an existing close-price CSV (for the Kronos baseline).

    python scripts/fetch_ohlcv.py data/prices_pit_2025-06-02_top100.csv data/ohlcv_pit_2025-06-02_top100.csv

Same source and adjustment as the close CSVs (Yahoo, auto_adjust=True), same date range; written long-format
(Date,Ticker,Open,High,Low,Close,Volume) with its sha256 printed for provenance. Refuses to overwrite.
"""
from __future__ import annotations

import hashlib
import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

BACKEND = Path(__file__).resolve().parents[1]


def main() -> None:
    src, dst = BACKEND / sys.argv[1], (BACKEND / sys.argv[2]).resolve()
    if dst.exists():
        raise SystemExit(f"{dst} exists; delete it first if you really mean to refetch")
    closes = pd.read_csv(src, index_col=0, parse_dates=True)
    tickers = list(closes.columns)
    start, end = closes.index[0].date(), closes.index[-1].date() + timedelta(days=1)
    frames = []
    for t in tickers:
        for attempt in range(4):
            df = yf.download(t, start=start.isoformat(), end=end.isoformat(), auto_adjust=True, progress=False,
                             multi_level_index=False)
            if len(df):
                break
            time.sleep(2 * (attempt + 1))
        if not len(df):
            raise SystemExit(f"no data for {t}")
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df.insert(0, "Ticker", t)
        frames.append(df)
    out = pd.concat(frames).rename_axis("Date").reset_index()
    out["Date"] = out["Date"].dt.date
    out.to_csv(dst, index=False, float_format="%.6f")
    print(sys.argv[2], len(out), "rows", hashlib.sha256(dst.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
