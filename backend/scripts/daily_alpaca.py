"""Alpaca SIP daily bars (official regular-session open and close, split- and dividend-adjusted) for D5's S&P 500
list and SPY: the independent source for D9's validity check (docs/PLAN_60_V2.md, round 5).

    python scripts/daily_alpaca.py        # writes data/intraday/sp500_daily_alpaca.parquet (Date, Ticker, Open, Close)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import httpx
import pandas as pd
from intraday_stocks import universe

from app.data_ingestion import bars

OUT = BACKEND / "data" / "intraday" / "sp500_daily_alpaca.parquet"


def fetch(c: httpx.Client, syms: list[str], start: str, end: str) -> list[dict[str, object]]:
    params: dict[str, str | int] = {"symbols": ",".join(s.replace("-", ".") for s in syms), "timeframe": "1Day",
                                    "start": start, "end": end, "feed": "sip", "adjustment": "all", "limit": 10000}
    rows: list[dict[str, object]] = []
    while True:
        for attempt in range(8):
            r = c.get(bars.ALPACA, params=params)
            if r.status_code != 429:
                break
            time.sleep(3 + 5 * attempt)
        r.raise_for_status()
        data = r.json()
        for sym, bs in (data.get("bars") or {}).items():
            rows += [{"Date": pd.Timestamp(b["t"]).tz_convert("America/New_York").tz_localize(None).normalize(),
                      "Ticker": sym.replace(".", "-"), "Open": b["o"], "Close": b["c"]} for b in bs]
        if not data.get("next_page_token"):
            return rows
        params["page_token"] = data["next_page_token"]


def main() -> None:
    kid, sec = bars.key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), bars.key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not kid or not sec:
        raise SystemExit("no Alpaca keys in backend/.env")
    syms = universe() + ["SPY"]
    rows: list[dict[str, object]] = []
    with httpx.Client(headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}, timeout=90) as c:
        for i in range(0, len(syms), 50):
            rows += fetch(c, syms[i:i + 50], "2015-12-01", "2026-09-25")
            print(f"  {min(i + 50, len(syms))}/{len(syms)} symbols, {len(rows):,} bars", flush=True)
    pd.DataFrame(rows).to_parquet(OUT)


if __name__ == "__main__":
    main()
