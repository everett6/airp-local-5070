"""Download 1-minute bars (Alpaca's free historical SIP feed, full market) for the day-trading track, once.

    python scripts/intraday_data.py                 # SPY and QQQ, 2016-01-04 to 2026-09-25
    python scripts/intraday_data.py --symbols SPY --start 2024-01-01

Regular hours only (09:30-15:59 ET bars, labelled by their start minute in New York time). Split-adjusted. Writes
data/intraday/<SYMBOL>_1min.parquet with columns ts (tz-aware, America/New_York), open, high, low, close, volume.
Needs the user's Alpaca keys in backend/.env (read by code, never printed).
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

OUT = BACKEND / "data" / "intraday"


def fetch(client: httpx.Client, symbol: str, start: str, end: str) -> pd.DataFrame:
    rows: list[dict] = []
    params = {"symbols": symbol, "timeframe": "1Min", "start": f"{start}T00:00:00Z", "end": f"{end}T23:59:00Z",
              "feed": "sip", "adjustment": "split", "limit": 10000}
    while True:
        for attempt in range(5):
            r = client.get(bars.ALPACA, params=params)
            if r.status_code == 429:
                time.sleep(2 + 3 * attempt)
                continue
            r.raise_for_status()
            break
        data = r.json()
        rows += data.get("bars", {}).get(symbol, [])
        tok = data.get("next_page_token")
        if not tok:
            break
        params["page_token"] = tok
        if len(rows) % 200_000 < 10_000:
            print(f"  {symbol}: {len(rows):,} bars, up to {rows[-1]['t'][:10]}", flush=True)
    df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    t = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    df = df[(t >= 9 * 60 + 30) & (t < 16 * 60)]
    return df[["ts", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2026-09-25")
    a = ap.parse_args()
    kid, sec = bars.key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), bars.key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not kid or not sec:
        raise SystemExit("no Alpaca keys in backend/.env")
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}, timeout=60) as c:
        for s in a.symbols.split(","):
            df = fetch(c, s, a.start, a.end)
            df.to_parquet(OUT / f"{s}_1min.parquet")
            print(f"{s}: {len(df):,} regular-hours bars, {df['ts'].dt.date.nunique()} days, "
                  f"{df['ts'].iloc[0]} .. {df['ts'].iloc[-1]}", flush=True)


if __name__ == "__main__":
    main()
