"""Six books by holding period: does Bonsai's BUY log-odds rank releases by their return over the sector ETF?

    python scripts/horizons6_eval.py

Fact-sheet decisions on the SEC-cross-checked sheets (results/events/decide_bonsai-27b_latest_factsheet*_secchk*),
2025-26 sample and 2024, each book on its own horizon. Pass rule: docs/WEEK_PLAN.md, "More horizons".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from event_eval import build
from secchk_eval import load

from app.sandbox.events import Prices, monthly_ic, quintile_spread

EV = BACKEND / "results" / "events"
BOOKS = {5: "1 week", 20: "1 month", 63: "3 months", 120: "6 months", 252: "1 year", 504: "2 years"}
YEARS = {"2025-26": ("factsheet_secchk", "data/events/events_sp500_2025.csv"),
         "2024": ("factsheet2024_secchk", "data/events/events_sp500_2024.csv")}


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    res = {}
    for year, (tag, events) in YEARS.items():
        df = build(pd.read_csv(BACKEND / events), p)
        for h, book in BOOKS.items():
            x = df.merge(load(tag, h), on="accession").dropna(subset=[f"fwd{h}"])
            k = f"{book:8s} ({h:3d}d) {year}"
            if len(x) < 50:
                print(f"{k:26s} n={len(x):5d}  too few outcomes known yet")
                continue
            res[k] = {**monthly_ic(x, "logodds", f"fwd{h}", min_n=10),
                      **{f"q_{a}": b for a, b in quintile_spread(x, "logodds", f"fwd{h}").items()}}
            v = res[k]
            if v["mean_ic"] is None:
                print(f"{k:26s} n={len(x):5d}  too few months")
                continue
            print(f"{k:26s} n={v['events']:5d} months={v['months']:3d} IC {v['mean_ic']:+.3f} "
                  f"[{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}]  top-bottom {v.get('q_spread_pct') or float('nan'):+.2f}%")
    (EV / "horizons6_eval.json").write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
