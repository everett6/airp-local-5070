"""D10's pre-market prices (docs/PLAN_60_V2.md, round 6): each trading day, each stock's last SIP trade at or before
09:25 ET within 08:00-09:25 (close of the last 1-minute bar starting at or before 09:24), split-adjusted.

    python scripts/premarket_stocks.py    # writes data/intraday/sp500_premarket.parquet (Date, Ticker, pre, t)
Resumable: one part file per month. 09:00-09:25 is fetched first; stocks with no trade there are looked up in
08:00-08:59.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import httpx
import pandas as pd
from intraday_stocks import universe

from app.data_ingestion import bars

OUT = BACKEND / "data" / "intraday" / "sp500_premarket.parquet"
PARTS = OUT.parent / "sp500_premarket_parts"


def last_trades(c: httpx.Client, syms: list[str], day: date, start: str, end: str) -> dict[str, tuple[float, str]]:
    """{symbol: (close, NY bar time)} of each symbol's last 1-minute bar starting in [start, end] (HH:MM)."""
    t0 = pd.Timestamp(f"{day} {start}", tz="America/New_York").tz_convert("UTC")
    t1 = pd.Timestamp(f"{day} {end}", tz="America/New_York").tz_convert("UTC")
    params: dict[str, str | int] = {"symbols": ",".join(s.replace("-", ".") for s in syms), "timeframe": "1Min",
                                    "start": t0.isoformat().replace("+00:00", "Z"),
                                    "end": t1.isoformat().replace("+00:00", "Z"), "feed": "sip",
                                    "adjustment": "split", "limit": 10000}
    out: dict[str, tuple[float, str]] = {}
    while True:
        for attempt in range(8):
            r = c.get(bars.ALPACA, params=params)
            if r.status_code != 429:
                break
            time.sleep(3 + 5 * attempt)
        r.raise_for_status()
        data = r.json()
        for sym, bs in (data.get("bars") or {}).items():
            b = max(bs, key=lambda x: x["t"])
            s = sym.replace(".", "-")
            if s not in out or b["t"] > out[s][1]:
                out[s] = (float(b["c"]), b["t"])
        if not data.get("next_page_token"):
            return out
        params["page_token"] = data["next_page_token"]


def main() -> None:
    kid, sec = bars.key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), bars.key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not kid or not sec:
        raise SystemExit("no Alpaca keys in backend/.env")
    syms = universe()
    days = pd.bdate_range("2016-01-04", "2026-09-24")
    months = sorted({d.strftime("%Y-%m") for d in days})
    PARTS.mkdir(parents=True, exist_ok=True)

    def one(m: str) -> None:
        f = PARTS / f"{m}.parquet"
        if f.exists():
            return
        rows = []
        with httpx.Client(headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}, timeout=90) as c:
            for d in days[days.strftime("%Y-%m") == m]:
                got = last_trades(c, syms, d.date(), "09:00", "09:24")
                if got:  # a holiday has no pre-market trades at all
                    rest = [s for s in syms if s not in got]
                    if rest:
                        got |= last_trades(c, rest, d.date(), "08:00", "08:59")
                rows += [{"Date": pd.Timestamp(d.date()), "Ticker": s, "pre": p, "t": t} for s, (p, t) in got.items()]
        pd.DataFrame(rows, columns=["Date", "Ticker", "pre", "t"]).to_parquet(f)
        print(f"  {m}: {len(rows):,}", flush=True)
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, months))
    pd.concat([pd.read_parquet(PARTS / f"{m}.parquet") for m in months], ignore_index=True).to_parquet(OUT)


if __name__ == "__main__":
    main()
