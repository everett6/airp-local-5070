"""Score Bonsai's three books (quick money 5 days, mid term 20, long term 120) with and without web research.

    python scripts/horizons_eval.py --features results/events/features_sp500_2025_research_Jan-v1-4B-GGUF_Q4_K_M.csv

Each book is scored on its own horizon: monthly rank IC between Bonsai's BUY log-odds and the return vs the sector
over that many trading days after entry, plus the top-minus-bottom fifth. Only releases that are in the research
table are used, so "with" and "without" research compare the same releases. 120-day outcomes exist only for entries
up to ~6 months before the price data ends. Writes results/events/horizons_eval.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from event_eval import build

from app.sandbox.events import Prices, monthly_ic, quintile_spread

BOOKS = {5: "quick money", 20: "mid term", 120: "long term"}


def decisions(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.DataFrame([json.loads(x) for x in path.read_text().splitlines()])[["accession", "logodds"]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", default="data/events/events_sp500_2025.csv")
    ap.add_argument("--features", required=True)
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--with-tag", default="jan_research")
    ap.add_argument("--without-tag", default="factsheet")
    args = ap.parse_args()
    feats = pd.read_csv(BACKEND / args.features)
    ev = pd.read_csv(BACKEND / args.events)
    df = build(ev[ev["accession"].isin(feats["accession"])], Prices.from_long(pd.read_parquet(BACKEND / args.prices)))
    res: dict[str, dict] = {}
    for h, book in BOOKS.items():
        for arm, tag in (("with research", args.with_tag), ("without research", args.without_tag)):
            suffix = "" if h == 20 else f"_h{h}"
            d = decisions(BACKEND / "results" / "events" / f"decide_bonsai-27b_latest_{tag}{suffix}.jsonl")
            if d is None:
                continue
            x = df.merge(d, on="accession").dropna(subset=[f"fwd{h}"])
            res[f"{book} ({h}d) | {arm}"] = {**monthly_ic(x, "logodds", f"fwd{h}", min_n=10),
                                              **{f"q_{k}": v for k, v in quintile_spread(x, "logodds", f"fwd{h}").items()}}
    (BACKEND / "results" / "events" / "horizons_eval.json").write_text(json.dumps(res, indent=1) + "\n")
    for k, v in res.items():
        if v["mean_ic"] is not None:
            print(f"{k:42s} n={v['events']:4d} months={v['months']:3d} IC {v['mean_ic']:+.3f} "
                  f"[{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}]  top-bottom {v['q_spread_pct']:+.2f}%")


if __name__ == "__main__":
    main()
