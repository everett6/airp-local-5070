"""Hourly perpetual funding rates from Deribit's public API, for trial C1 (docs/PLAN_60_V2.md "Crypto funding carry C1").

    python scripts/funding_data.py      # writes data/crypto/funding_deribit.parquet (ts UTC, asset, rate)

`rate` is Deribit's `interest_1h`: the funding a short perpetual receives for that hour when positive. Free, no key.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import pandas as pd

URL = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
OUT = BACKEND / "data" / "crypto" / "funding_deribit.parquet"
ASSETS = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}


def fetch(c: httpx.Client, inst: str, t0: int, t1: int) -> list[dict[str, float]]:
    for attempt in range(8):
        r = c.get(URL, params={"instrument_name": inst, "start_timestamp": t0, "end_timestamp": t1})
        if r.status_code not in (429, 500, 502, 503, 504):
            break
        time.sleep(2 + 3 * attempt)
    r.raise_for_status()
    return list(r.json().get("result") or [])


def main() -> None:
    start, end = pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2026-09-25", tz="UTC")
    rows = []
    with httpx.Client(timeout=60) as c:
        for asset, inst in ASSETS.items():
            t = start
            while t < end:
                t1 = min(t + pd.Timedelta(days=30), end)
                got = fetch(c, inst, int(t.timestamp() * 1000), int(t1.timestamp() * 1000))
                rows += [{"ts": pd.Timestamp(x["timestamp"], unit="ms", tz="UTC"), "asset": asset,
                          "rate": float(x["interest_1h"])} for x in got]
                t = t1
                time.sleep(0.2)
            print(f"  {asset}: {sum(r['asset'] == asset for r in rows):,} hours", flush=True)
    df = pd.DataFrame(rows).drop_duplicates(["ts", "asset"]).sort_values(["asset", "ts"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)


if __name__ == "__main__":
    main()
