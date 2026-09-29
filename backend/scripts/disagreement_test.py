"""Disagreement test (docs/PLAN_60_V2.md "Disagreement test", spec fixed before the run).

    python scripts/disagreement_test.py

D = rank(Bonsai's 1-week log-odds) - rank(earnings-day reaction vs sector), percentiles within the entry month.
Outcome: excess return vs the sector ETF over the 20 trading days after the reaction day (fwd20_ear). Nothing is
fitted, so 2024 and 2025-26 are both out of sample. Pass: pooled monthly IC 95% CI above 0, mean IC positive in each
period, and the top-minus-bottom fifth net of 0.4% with a 90% CI above 0. Writes results/events/disagreement_test.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import build

from app.sandbox.dsr import register
from app.sandbox.events import Prices, monthly_ic

EV = BACKEND / "results" / "events"
COST = 0.004  # 20 bps each way on each leg of a 20-day long-short
SAMPLES = {"2024": ("data/events/events_sp500_2024.csv", "decide_bonsai-27b_latest_factsheet2024_secchk_h5.jsonl"),
           "2025-26": ("data/events/events_sp500_2025.csv", "decide_bonsai-27b_latest_factsheet_secchk_h5.jsonl")}


def load(p: Prices) -> pd.DataFrame:
    frames = []
    for period, (events, decisions) in SAMPLES.items():
        df = build(pd.read_csv(BACKEND / events), p)
        dec = pd.DataFrame([json.loads(x) for x in (EV / decisions).read_text().splitlines()])[["accession", "logodds"]]
        frames.append(df[df["scorable"]].merge(dec, on="accession").assign(period=period))
    d = pd.concat(frames, ignore_index=True).dropna(subset=["logodds", "ear"])
    g = d.groupby("month")
    d["D"] = g["logodds"].rank(pct=True) - g["ear"].rank(pct=True)
    return d


def spread_net(df: pd.DataFrame, signal: str, outcome: str, n_boot: int = 5000) -> dict[str, float | int | None]:
    d = df.dropna(subset=[signal, outcome])
    s = []
    for _, g in d.groupby("month"):
        if len(g) >= 20:
            q = g[signal].rank(pct=True)
            s.append(float(g.loc[q > 0.8, outcome].mean() - g.loc[q <= 0.2, outcome].mean()) - COST)
    if not s:
        return {"months": 0, "net_pct": None, "ci90_lo": None, "ci90_hi": None}
    a = np.array(s)
    boots = a[np.random.default_rng(0).integers(0, len(a), (n_boot, len(a)))].mean(1)
    lo, hi = np.percentile(boots, [5, 95])
    return {"months": len(a), "net_pct": round(100 * float(a.mean()), 3), "ci90_lo": round(100 * float(lo), 3),
            "ci90_hi": round(100 * float(hi), 3)}


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    d = load(p)
    out: dict = {"events": len(d), "by_period": {k: int(v) for k, v in d["period"].value_counts().items()}}
    out["pooled"] = {"D": monthly_ic(d, "D", "fwd20_ear"), "D_spread_net": spread_net(d, "D", "fwd20_ear"),
                     "bonsai_alone": monthly_ic(d, "logodds", "fwd20_ear"),
                     "reaction_alone": monthly_ic(d, "ear", "fwd20_ear"),
                     "D_60d": monthly_ic(d, "D", "fwd60_ear")}
    out["periods"] = {k: {"D": monthly_ic(g, "D", "fwd20_ear"), "D_spread_net": spread_net(g, "D", "fwd20_ear")}
                      for k, g in d.groupby("period")}
    pooled, per = out["pooled"], out["periods"]
    out["checks"] = {"pooled_ic_ci_above_0": bool((pooled["D"]["ci_lo"] or 0) > 0),
                     "positive_each_period": bool(all((v["D"]["mean_ic"] or 0) > 0 for v in per.values())),
                     "net_spread_ci90_above_0": bool((pooled["D_spread_net"]["ci90_lo"] or 0) > 0)}
    out["pass"] = all(out["checks"].values())
    (EV / "disagreement_test.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "disagreement_bonsai_vs_reaction", "date": time.strftime("%Y-%m-%d"), "kind": "signal_ic",
              "ic": pooled["D"]["mean_ic"], "result": "pass" if out["pass"] else "fail"})
    print(json.dumps(out, indent=1))
    print("verdict (pre-registered):", "PASS" if out["pass"] else "FAIL")


if __name__ == "__main__":
    main()
