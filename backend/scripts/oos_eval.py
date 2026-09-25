"""Out-of-sample check of the two leads Stage A left open (docs/PLAN_V2.md), on any year's research table.

    python scripts/oos_eval.py --events data/events/events_sp500_2024.csv --features results/events/features_sp500_2024.csv \
        --decide results/events/decide_bonsai-27b_latest_xbrl2024.jsonl --name 2024

Leads, fixed before looking at 2024:
  L1  EPS change where the reader verified BOTH numbers in the press release (2025-26: 20-day IC +0.21)
  L2  Bonsai-27B's P(BUY) reading the research-table fact sheet (2025-26: 20-day IC +0.077)
Also EPS change where the SEC tool supplied the year-earlier number, and all pairs. Monthly rank IC vs the sector,
20 and 60 days, same scorer as everywhere else. Bonsai's 2024 decisions fall inside its training data, so a 2024 pass
for L2 is weak evidence (it may remember), while a fail is informative; L1 is code over read numbers and is clean.
Writes results/events/oos_<name>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import build

from app.sandbox.events import Prices, monthly_ic, quintile_spread


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--decide", default=None)
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    p = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    df = build(pd.read_csv(BACKEND / args.events), p)
    f = pd.read_csv(BACKEND / args.features)
    f["eps_change"] = np.clip((f["eps_q"] - f["eps_prior"]) / f["eps_prior"].abs().replace(0, np.nan), -2, 2)
    d = df.merge(f[["accession", "eps_change", "eps_prior_source"]], on="accession")
    if args.decide:
        dec = pd.DataFrame([json.loads(x) for x in (BACKEND / args.decide).read_text().splitlines()])
        d = d.merge(dec[["accession", "logodds"]], on="accession", how="left")
    tests = {"L1 eps_change, reader pairs": (d[d["eps_prior_source"] == "reader"], "eps_change"),
             "eps_change, SEC-completed pairs": (d[d["eps_prior_source"] == "sec"], "eps_change"),
             "eps_change, all pairs": (d, "eps_change")}
    if "logodds" in d:
        tests["L2 Bonsai P(BUY), research table"] = (d, "logodds")
    res = {"name": args.name, "releases": len(d), "tests": {}}
    for label, (x, s) in tests.items():
        for h in ("fwd20", "fwd60"):
            y = x.dropna(subset=[s, h])
            res["tests"][f"{label} | {h}"] = {**monthly_ic(y, s, h, min_n=10),
                                               **{f"q_{k}": v for k, v in quintile_spread(y, s, h).items()}}
    (BACKEND / "results" / "events" / f"oos_{args.name}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(f"{args.name}: {len(d)} releases")
    for k, v in res["tests"].items():
        if v["mean_ic"] is not None:
            print(f"  {k:48s} n={v['events']:5d} months={v['months']:3d} IC {v['mean_ic']:+.3f} "
                  f"[{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}]  top-bottom {v['q_spread_pct']:+.2f}%")


if __name__ == "__main__":
    main()
