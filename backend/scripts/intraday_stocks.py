"""Download the 30-minute bars D5 needs (docs/PLAN_60_V2.md "Day-trading round 3"), once.

    python scripts/intraday_stocks.py            # the 2024 S&P 500 list, 2016-01-04 .. 2026-09-25

Alpaca's free historical SIP feed, split-adjusted. One request window per trading day, 14:30-16:00 New York time, for
all symbols at once (a whole-history request pages about three weeks of one symbol at a time, far too slow). Only the
14:30 and 15:30 bars are kept (D5 uses the 15:00 price, and the 15:30 bar's open and close). Months are saved as they
finish, so a rerun resumes. Writes data/intraday/sp500_30min.parquet
(ts, symbol, open, close, n = trade count). Needs the user's Alpaca keys in backend/.env (read by code, never printed).
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import pandas as pd

from app.data_ingestion import bars
from app.data_ingestion.tickers import trading_symbol

OUT = BACKEND / "data" / "intraday" / "sp500_30min.parquet"
PARTS = OUT.parent / "sp500_30min_parts"  # one file per month
HISTORY = BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv"


def universe() -> list[str]:
    ev = pd.read_csv(HISTORY)
    ev = ev[(ev["index"] == "sp500") & (ev["accepted_utc"].str[:4] == "2024")]
    return sorted({trading_symbol(t) for t in ev["ticker"]})


def fetch_day(client: httpx.Client, symbols: list[str], day: date) -> list[dict[str, object]]:
    """The 14:30, 15:00 and 15:30 bars of one day (none on holidays); keeps 14:30 and 15:30."""
    t0 = pd.Timestamp(f"{day} 14:30", tz="America/New_York").tz_convert("UTC")
    # Alpaca spells class shares with a dot (BRK.B)
    params: dict[str, str | int] = {"symbols": ",".join(s.replace("-", ".") for s in symbols), "timeframe": "30Min",
                                    "start": t0.isoformat().replace("+00:00", "Z"),
                                    "end": (t0 + pd.Timedelta(minutes=89)).isoformat().replace("+00:00", "Z"),
                                    "feed": "sip", "adjustment": "split", "limit": 10000}
    rows: list[dict[str, object]] = []
    while True:
        for attempt in range(8):
            r = client.get(bars.ALPACA, params=params)
            if r.status_code != 429:
                break
            time.sleep(3 + 5 * attempt)
        r.raise_for_status()
        data = r.json()
        for sym, bs in (data.get("bars") or {}).items():
            for b in bs:
                t = pd.Timestamp(b["t"]).tz_convert("America/New_York")
                if (t.hour, t.minute) in ((14, 30), (15, 30)):
                    rows.append({"ts": t, "symbol": sym.replace(".", "-"), "open": b["o"], "close": b["c"], "n": b["n"]})
        tok = data.get("next_page_token")
        if not tok:
            return rows
        params["page_token"] = tok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2026-09-25")
    a = ap.parse_args()
    kid, sec = bars.key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), bars.key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not kid or not sec:
        raise SystemExit("no Alpaca keys in backend/.env")
    syms = universe()
    days = pd.bdate_range(a.start, a.end)
    months = sorted({d.strftime("%Y-%m") for d in days})
    PARTS.mkdir(parents=True, exist_ok=True)

    def one(m: str) -> None:
        f = PARTS / f"{m}.parquet"
        if f.exists():
            return
        rows: list[dict[str, object]] = []
        with httpx.Client(headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}, timeout=90) as c:
            for d in days[days.strftime("%Y-%m") == m]:
                rows += fetch_day(c, syms, d.date())
        pd.DataFrame(rows, columns=["ts", "symbol", "open", "close", "n"]).to_parquet(f)
        print(f"  {m}: {len(rows):,} bars", flush=True)
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, months))
    df = pd.concat([pd.read_parquet(PARTS / f"{m}.parquet") for m in months], ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)
    print(f"{len(syms)} symbols, {df['symbol'].nunique()} with data, {len(df):,} bars -> {OUT.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
