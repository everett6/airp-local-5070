"""Do analyst expectations (from archived Yahoo quote pages, before each release) predict each book's outcome?

    python scripts/analyst_eval.py
    python scripts/analyst_eval.py --events data/events/events_sp500_2024.csv

Features, all known before the SEC acceptance time (data/events/analyst_targets.jsonl, scripts/fetch_analyst_targets.py):
  target_upside   average analyst target / last close before the entry day - 1
  dispersion      (high target - low target) / average target
  net_actions     target raises - target cuts listed on the page
  net_headlines   upgrade headlines - downgrade headlines about the company
Each feature is scored per book on its own horizon (monthly rank IC vs the return over the sector ETF, as in
horizons_eval.py). Pre-registered direction (docs/WEEK_PLAN.md): positive for all but dispersion (negative).
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
FEATURES = {"target_upside": +1, "dispersion": -1, "net_actions": +1, "net_headlines": +1}


def features(targets: pd.DataFrame, df: pd.DataFrame, p: Prices) -> pd.DataFrame:
    x = df[df["scorable"]].merge(targets[targets["ok"]], on="accession", suffixes=("", "_t"))
    days = pd.DatetimeIndex(p.close.index)

    def last_close(r: pd.Series) -> float | None:
        i = days.get_loc(pd.Timestamp(r["entry"])) - 1
        v = p.close[r["ticker"]].iloc[i] if i >= 0 else None
        return float(v) if v is not None and pd.notna(v) else r.get("price_on_page")

    x["close_before"] = x.apply(last_close, axis=1)
    x["target_upside"] = x["target_avg"] / x["close_before"] - 1
    x["dispersion"] = (x["target_high"] - x["target_low"]) / x["target_avg"]
    x["net_actions"] = x["raises"].fillna(0) - x["lowers"].fillna(0)
    x["net_headlines"] = x["headline_up"].fillna(0) - x["headline_down"].fillna(0)
    return x


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", default="data/events/events_sp500_2025.csv")
    ap.add_argument("--targets", default="data/events/analyst_targets.jsonl")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--out", default="results/events/analyst_eval.json")
    args = ap.parse_args()
    rows = [json.loads(x) for x in (BACKEND / args.targets).read_text().splitlines()]
    targets = pd.DataFrame(list({r["accession"]: r for r in rows}.values()))  # a retry's row replaces the old one
    ev = pd.read_csv(BACKEND / args.events)
    df = build(ev[ev["accession"].isin(targets["accession"])], Prices.from_long(pd.read_parquet(BACKEND / args.prices)))
    x = features(targets, df, Prices.from_long(pd.read_parquet(BACKEND / args.prices)))
    print(f"{len(targets)} releases looked up, {int(targets['ok'].sum())} with a quote page, {len(x)} scorable; "
          f"with a target {int(x['target_avg'].notna().sum())}, with a listed action "
          f"{int((x['raises'].fillna(0) + x['lowers'].fillna(0) > 0).sum())}")
    res: dict[str, dict] = {}
    for h, book in BOOKS.items():
        for f, sign in FEATURES.items():
            y = x.dropna(subset=[f, f"fwd{h}"]).assign(score=lambda d, f=f, s=sign: s * d[f])
            if len(y) < 50:
                continue
            k = f"{book} ({h}d) | {f}{'' if sign > 0 else ' (negated)'}"
            res[k] = {**monthly_ic(y, "score", f"fwd{h}", min_n=10),
                      **{f"q_{a}": b for a, b in quintile_spread(y, "score", f"fwd{h}").items()}}
    (BACKEND / args.out).write_text(json.dumps(res, indent=1) + "\n")
    for k, v in res.items():
        if v["mean_ic"] is not None:
            print(f"{k:48s} n={v['events']:4d} months={v['months']:3d} IC {v['mean_ic']:+.3f} "
                  f"[{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}]  top-bottom {v.get('q_spread_pct') or float('nan'):+.2f}%")


if __name__ == "__main__":
    main()
