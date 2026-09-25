"""Daily OHLCV (free, Yahoo) for every S&P 1500 member in the event window, SPY, and the 11 SPDR sector ETFs.

    python scripts/fetch_event_prices.py --members data/events/members_2024_2026.csv --start 2023-01-01 --end 2026-09-25

Same source and adjustment as the other price files (auto_adjust=True). Writes data/events/ohlcv_<start>_<end>.parquet
and a meta file listing tickers Yahoo no longer has (delisted, acquired, renamed): events of those companies
can't be scored, which is recorded rather than hidden. Batches of 50 with pauses, and one retry pass, because Yahoo
throttles bursts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

BACKEND = Path(__file__).resolve().parents[1]
# GICS sector (as Wikipedia writes it) -> SPDR sector ETF: the benchmark each event's return is measured against
SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}


def batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False, threads=True,
                      group_by="ticker")
    for t in tickers:
        try:
            df = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except KeyError:
            continue
        if len(df):
            out[t] = df
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", default="data/events/members_2024_2026.csv")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-09-25")
    args = ap.parse_args()
    dst = BACKEND / "data" / "events" / f"ohlcv_{args.start}_{args.end}.parquet"
    if dst.exists():
        raise SystemExit(f"{dst} exists; delete it to refetch")
    mem = pd.read_csv(BACKEND / args.members)
    tickers = sorted({str(t).replace(".", "-") for t in mem["ticker"]} | {"SPY", *SECTOR_ETF.values()})
    bars: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), 50):
        bars |= batch(tickers[i:i + 50], args.start, args.end)
        print(f"{min(i + 50, len(tickers))}/{len(tickers)} requested, {len(bars)} with data", flush=True)
        time.sleep(2)
    missing = [t for t in tickers if t not in bars]
    for t in missing:  # one slow retry: batch downloads drop symbols when throttled
        time.sleep(1.5)
        one = batch([t], args.start, args.end)
        bars |= one
    missing = [t for t in tickers if t not in bars]
    long = pd.concat([df.assign(Ticker=t) for t, df in bars.items()]).rename_axis("Date").reset_index()
    long["Date"] = pd.to_datetime(long["Date"]).dt.date.astype(str)
    long = long[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]].sort_values(["Ticker", "Date"])
    long.to_parquet(dst, index=False)
    meta = {"fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), "source": "Yahoo via yfinance, auto_adjust",
            "window": [args.start, args.end], "tickers_requested": len(tickers), "tickers_with_data": len(bars),
            "missing": missing, "sector_etfs": SECTOR_ETF, "rows": len(long),
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()}
    dst.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: meta[k] for k in ("tickers_requested", "tickers_with_data", "rows")}), "missing:",
          len(missing))


if __name__ == "__main__":
    main()
