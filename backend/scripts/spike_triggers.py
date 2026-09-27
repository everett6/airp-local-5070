"""PLAN_60_V2 Mon 28 item 3: every historical spike-check trigger in 2024-26 (docs/STRATEGY_RESEARCH.md, "Spike check").

    python scripts/spike_triggers.py

A trigger on day d (checked at d's close): an asset held at d's open (the master book with the 20-day Bonsai
satellite, 10 bps, as in scripts/satellite20_test.py) whose close-to-close return on d is above 4x its 60-day daily
volatility (the 60 returns before d), or SPY / BTC-USD above 3x. Writes results/spike_triggers.csv (one row per trigger,
with the 5-day forward return from d+1's open, the arm A/B scoring input for Tue).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from master_portfolio import full_run, load_crypto
from satellite20_test import MERGED, merge

from app.sandbox.events import Prices

SIGMA = {"SPY": 3.0, "BTC-USD": 3.0}


def main() -> None:
    merge()
    args = argparse.Namespace(events="data/events/events_sp500_2024_2026.csv",
                              prices="data/events/ohlcv_2023-01-01_2026-09-25.parquet", start="2024-01-02",
                              end="2026-09-25", crypto_cap=0.20, decide=MERGED, horizon=20, book=[],
                              min_calibration=300, cost_bps=10.0, sizing="kelly", pick_weight=0.025)
    _, sims = full_run(args)
    stocks = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    crypto = Prices.from_long(load_crypto(args.end))
    cc = ["BTC-USD", "ETH-USD"]
    close = stocks.close.join(crypto.close[cc], how="left")
    opn = stocks.open.join(crypto.open[cc], how="left")
    ret = close.pct_change()
    vol = ret.rolling(60).std().shift(1)
    rows = []
    for w in sims["master"].weights_log:
        d = pd.Timestamp(w["day"])
        if d not in ret.index:
            continue
        i = ret.index.get_loc(d)
        for a, wt in w["targets"].items():
            if wt <= 0 or a not in ret.columns or pd.isna(vol.at[d, a]) or vol.at[d, a] == 0:
                continue
            z = ret.at[d, a] / vol.at[d, a]
            if abs(z) <= SIGMA.get(a, 4.0):
                continue
            fwd = None
            if i + 6 < len(opn.index) and pd.notna(opn[a].iloc[i + 1]) and pd.notna(opn[a].iloc[i + 6]):
                fwd = float(opn[a].iloc[i + 6] / opn[a].iloc[i + 1] - 1)
            rows.append({"day": d.date(), "asset": a, "weight": wt, "ret_pct": round(100 * ret.at[d, a], 2),
                         "z": round(float(z), 2), "fwd5_pct": None if fwd is None else round(100 * fwd, 2)})
    df = pd.DataFrame(rows).drop_duplicates(["day", "asset"])
    df.to_csv(BACKEND / "results" / "spike_triggers.csv", index=False)
    print(f"{len(df)} triggers on {df['day'].nunique()} days; by asset type:")
    print(df["asset"].map(lambda a: a if a in ("SPY", "BTC-USD", "ETH-USD") else "stock").value_counts().to_string())
    print(df.assign(y=pd.to_datetime(df["day"]).dt.year).groupby("y").size().to_string())


if __name__ == "__main__":
    main()
