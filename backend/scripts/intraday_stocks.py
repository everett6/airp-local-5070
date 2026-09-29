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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import pandas as pd

from app.data_ingestion import bars
from app.data_ingestion.tickers import trading_symbol

OUT = BACKEND / "data" / "intraday" / "sp500_30min.parquet"
PARTS = OUT.parent / "sp500_30min_parts"  # one file per batch, so a rerun resumes
HISTORY = BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv"


def universe() -> list[str]:
    ev = pd.read_csv(HISTORY)
    ev = ev[(ev["index"] == "sp500") & (ev["accepted_utc"].str[:4] == "2024")]
    return sorted({trading_symbol(t) for t in ev["ticker"]})


def fetch(client: httpx.Client, symbols: list[str], start: str, end: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    # Alpaca spells class shares with a dot (BRK.B)
    params: dict[str, str | int] = {"symbols": ",".join(s.replace("-", ".") for s in symbols), "timeframe": "30Min",
                                    "start": f"{start}T00:00:00Z", "end": f"{end}T23:59:00Z", "feed": "sip",
                                    "adjustment": "split", "limit": 10000}
    while True:
        for attempt in range(6):
            r = client.get(bars.ALPACA, params=params)
            if r.status_code == 429:
                time.sleep(3 + 5 * attempt)
                continue
            first = "page_token" not in params
            if r.status_code == 400 and first and len(symbols) > 1:  # an unknown symbol: split the batch
                h = len(symbols) // 2
                return pd.concat([fetch(client, symbols[:h], start, end), fetch(client, symbols[h:], start, end)])
            if r.status_code == 400 and first:
                print(f"  {symbols[0]}: no data ({r.text[:80]})", flush=True)
                return pd.DataFrame(rows, columns=["ts", "symbol", "open", "close"])
            r.raise_for_status()
            break
        data = r.json()
        for sym, bs in (data.get("bars") or {}).items():
            for b in bs:
                t = pd.Timestamp(b["t"]).tz_convert("America/New_York")
                if (t.hour, t.minute) in ((14, 30), (15, 30)):
                    rows.append({"ts": t, "symbol": sym.replace(".", "-"), "open": b["o"], "close": b["c"]})
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
    batches = [syms[i:i + 25] for i in range(0, len(syms), 25)]
    PARTS.mkdir(parents=True, exist_ok=True)

    def one(k: int) -> None:
        f = PARTS / f"{k:03d}.parquet"
        if f.exists():
            return
        with httpx.Client(headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}, timeout=90) as c:
            fetch(c, batches[k], a.start, a.end).to_parquet(f)
        print(f"  batch {k + 1}/{len(batches)} done", flush=True)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, range(len(batches))))
    parts = [pd.read_parquet(PARTS / f"{k:03d}.parquet") for k in range(len(batches))]
    df = pd.concat(parts, ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)
    print(f"{len(syms)} symbols, {df['symbol'].nunique()} with data, {len(df):,} bars -> {OUT.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
