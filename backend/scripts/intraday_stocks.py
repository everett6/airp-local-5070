"""Download the 30-minute bars D5 needs (docs/PLAN_60_V2.md "Day-trading round 3"), once.

    python scripts/intraday_stocks.py            # the 2024 S&P 500 list, 2016-01-04 .. 2026-09-25

Alpaca's free historical SIP feed, split-adjusted, many symbols per request. Only the 14:30 and 15:30 New York bars
are kept (D5 uses the 15:00 price, and the 15:30 bar's open and close). Writes data/intraday/sp500_30min.parquet
(ts, symbol, open, close). Needs the user's Alpaca keys in backend/.env (read by code, never printed).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import pandas as pd

from app.data_ingestion import bars
from app.data_ingestion.tickers import trading_symbol

OUT = BACKEND / "data" / "intraday" / "sp500_30min.parquet"
HISTORY = BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv"


def universe() -> list[str]:
    ev = pd.read_csv(HISTORY)
    ev = ev[(ev["index"] == "sp500") & (ev["accepted_utc"].str[:4] == "2024")]
    return sorted({trading_symbol(t) for t in ev["ticker"]})


def fetch(client: httpx.Client, symbols: list[str], start: str, end: str) -> pd.DataFrame:
    rows: list[dict] = []
    params = {"symbols": ",".join(symbols), "timeframe": "30Min", "start": f"{start}T00:00:00Z",
              "end": f"{end}T23:59:00Z", "feed": "sip", "adjustment": "split", "limit": 10000}
    while True:
        for attempt in range(6):
            r = client.get(bars.ALPACA, params=params)
            if r.status_code == 429:
                time.sleep(3 + 5 * attempt)
                continue
            r.raise_for_status()
            break
        data = r.json()
        for sym, bs in (data.get("bars") or {}).items():
            for b in bs:
                t = pd.Timestamp(b["t"]).tz_convert("America/New_York")
                if (t.hour, t.minute) in ((14, 30), (15, 30)):
                    rows.append({"ts": t, "symbol": sym, "open": b["o"], "close": b["c"]})
        tok = data.get("next_page_token")
        if not tok:
            break
        params["page_token"] = tok
    return pd.DataFrame(rows, columns=["ts", "symbol", "open", "close"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2026-09-25")
    a = ap.parse_args()
    kid, sec = bars.key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), bars.key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not kid or not sec:
        raise SystemExit("no Alpaca keys in backend/.env")
    syms = universe()
    parts = []
    with httpx.Client(headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}, timeout=90) as c:
        for i in range(0, len(syms), 25):
            parts.append(fetch(c, syms[i:i + 25], a.start, a.end))
            print(f"  {min(i + 25, len(syms))}/{len(syms)} symbols, {sum(len(p) for p in parts):,} bars", flush=True)
    df = pd.concat(parts, ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)
    print(f"{len(syms)} symbols, {df['symbol'].nunique()} with data, {len(df):,} bars -> {OUT.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
