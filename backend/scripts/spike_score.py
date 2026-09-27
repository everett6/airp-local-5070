"""Spike check score (docs/PLAN_60_V2.md "Tue 29 item 1", fixed before any label existed).

    python scripts/spike_score.py

Arm A: no response. Arm B: an unexplained spike halves the position for 5 days; every other label: no change.
Per trigger, B - A = -0.5 * fwd5 - 0.5 * 2 * 10 bps if unexplained, else 0. Pass: mean(B - A) has a 95% CI above 0
(bootstrap clustered by trigger day) AND at least 20 triggers are labeled unexplained (fewer: untestable).
Writes results/spike_score.json and registers one trial.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.sandbox.dsr import register

COST = 10 / 1e4


def main() -> None:
    df = pd.read_csv(BACKEND / "results" / "spike_causes.csv").dropna(subset=["fwd5_pct"])
    df["fwd5"] = df["fwd5_pct"] / 100
    une = df["label"] == "unexplained"
    df["diff"] = np.where(une, -0.5 * df["fwd5"] - 0.5 * 2 * COST, 0.0)
    days = df["day"].unique()
    rng = np.random.default_rng(0)
    groups = {d: g["diff"].to_numpy() for d, g in df.groupby("day")}
    boots = []
    for _ in range(2000):
        pick = rng.choice(days, len(days))
        boots.append(np.concatenate([groups[d] for d in pick]).mean())
    lo, hi = np.percentile(boots, [2.5, 97.5])
    n_une = int(une.sum())
    verdict = "untestable" if n_une < 20 else ("pass" if lo > 0 else "fail")
    out = {"triggers_scored": len(df), "unexplained": n_une, "mean_diff_pct": round(100 * df["diff"].mean(), 3),
           "ci95_pct": [round(100 * lo, 3), round(100 * hi, 3)], "verdict": verdict,
           "labels": df["label"].value_counts().to_dict(),
           "mean_fwd5_pct_by_label": df.groupby("label")["fwd5_pct"].mean().round(2).to_dict(),
           "overruled_pct": round(100 * float((df["label"] != df["label_model"]).mean()), 1)}
    (BACKEND / "results" / "spike_score.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "spike_check_analyze_first", "date": time.strftime("%Y-%m-%d"), "kind": "overlay",
              "window": "2024-2026", "result": verdict})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
