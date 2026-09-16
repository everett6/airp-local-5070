"""Lookahead-propensity interaction test (Gao et al., arXiv:2512.23847) for one arm of a finished run.

    python scripts/lap_test.py v7_phase_f llm_fund_lp

Linear probability model on the run's post-warm-up rows:  up = a + b*s + c*LAP + d*(s*LAP),  s = p - 0.5.
If the forecasts lean on remembered outcomes, they are more accurate where the model remembers more: d > 0.
The 95% CI for d resamples whole weeks. Also reports how often the model's date-only recall got the direction
right in this window (chance ~50% if the window is after its training cutoff).
Writes results/lap_test_<tag>_<arm>.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.dashboard import data as D


def interaction_test(rows: list[dict[str, Any]], n_boot: int = 2000, seed: int = 0) -> dict[str, Any]:
    rows = [r for r in rows if r.get("lap") is not None and np.isfinite(r["lap"])]
    weeks = sorted({r["cutoff"] for r in rows})
    by_week = {w: [r for r in rows if r["cutoff"] == w] for w in weeks}

    def fit(rs: list[dict[str, Any]]) -> np.ndarray:
        s = np.array([r["p"] - 0.5 for r in rs])
        lap = np.array([r["lap"] for r in rs])
        x = np.column_stack([np.ones(len(rs)), s, lap, s * lap])
        y = np.array([float(r["up"]) for r in rs])
        coef: np.ndarray = np.linalg.lstsq(x, y, rcond=None)[0]
        return coef

    coef = fit(rows)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(weeks), len(weeks))
        boots.append(fit([r for i in pick for r in by_week[weeks[i]]])[3])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    recall = [float((r["p_up_recall"] > 0.5) == bool(r["up"])) for r in rows if r["p_up_recall"] != 0.5]
    return {"n": len(rows), "weeks": len(weeks), "coef": {"a": coef[0], "b_signal": coef[1], "c_lap": coef[2],
            "d_signal_x_lap": coef[3]}, "d_ci": [float(lo), float(hi)], "leak_signature": bool(lo > 0),
            "mean_lap": float(np.mean([r["lap"] for r in rows])),
            "recall_direction_accuracy": float(np.mean(recall)) if recall else None, "recall_n": len(recall)}


def main() -> None:
    tag, arm = sys.argv[1], sys.argv[2]
    results = BACKEND / "results"
    report = D.load_report(tag, results)
    preds = D.warm_filter(D.load_predictions(tag, results), report, True)
    lap = {(r["cutoff"], r["ticker"]): r for r in map(json.loads, (results / f"lap_{tag}.jsonl").read_text().splitlines())}
    rows = []
    for r in preds[preds["arm"] == arm].to_dict("records"):
        cut = str(r["cutoff"])[:10]
        m = lap.get((cut, r["ticker"]))
        if m:
            rows.append({"cutoff": cut, "p": float(r["p"]), "up": bool(r["up"]), "lap": m["lap"],
                         "p_up_recall": m["p_up_recall"]})
    out = {"tag": tag, "arm": arm, **interaction_test(rows)}
    (results / f"lap_test_{tag}_{arm}.json").write_text(json.dumps(out, indent=2, default=float) + "\n")
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
