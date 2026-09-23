"""Lookahead-propensity interaction test (Gao et al., arXiv:2512.23847) for one arm of a finished run.

    python scripts/lap_test.py v7_phase_f llm_fund_lp

Linear probability model on the run's post-warm-up rows:  up = a + b*s + c*LAP + d*(s*LAP),  s = p - 0.5.
If the forecasts lean on remembered outcomes, they are more accurate where the model remembers more: d > 0.
The 95% CI for d resamples whole weeks (contiguous blocks of weeks when outcome windows overlap). Also reports how often the model's date-only recall got the direction
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
from app.sandbox.scoring import boot_indices


def interaction_test(rows: list[dict[str, Any]], n_boot: int = 2000, seed: int = 0, block: int = 1) -> dict[str, Any]:
    rows = [r for r in rows if r.get("lap") is not None and np.isfinite(r["lap"])]
    weeks = sorted({r["cutoff"] for r in rows})
    week_ix = {w: i for i, w in enumerate(weeks)}
    s = np.array([r["p"] - 0.5 for r in rows])
    lap = np.array([r["lap"] for r in rows])
    x = np.column_stack([np.ones(len(rows)), s, lap, s * lap])
    y = np.array([float(r["up"]) for r in rows])
    if len(weeks) < 2:
        return {"n": len(rows), "weeks": len(weeks), "uninformative": True, "lap_sd": None,
                "coef": None, "d_ci": None, "leak_signature": False,
                "bootstrap_block": block, "mean_lap": None, "recall_direction_accuracy": None, "recall_n": 0,
                "note": "fewer than 2 weeks with LAP values: no test"}
    wk = np.array([week_ix[r["cutoff"]] for r in rows])
    # OLS through per-week normal equations: resampling weeks = summing their X'X and X'y, so the 2,000 bootstrap
    # fits need no copies of the rows (same estimate as least squares on the stacked resampled rows)
    xtx = np.zeros((len(weeks), 4, 4))
    xty = np.zeros((len(weeks), 4))
    np.add.at(xtx, wk, x[:, :, None] * x[:, None, :])
    np.add.at(xty, wk, x * y[:, None])
    coef = np.linalg.pinv(xtx.sum(axis=0)) @ xty.sum(axis=0)
    rng = np.random.default_rng(seed)
    pick = boot_indices(rng, len(weeks), n_boot, block)
    boots = (np.linalg.pinv(xtx[pick].sum(axis=1)) @ xty[pick].sum(axis=1)[:, :, None])[:, 3, 0]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    recall = [float((r["p_up_recall"] > 0.5) == bool(r["up"])) for r in rows if r["p_up_recall"] != 0.5]
    # If the model almost never claims to remember, LAP is ~0 everywhere: the interaction is unidentified and
    # "no leak signature" would be vacuous. Say so instead of reporting a clean bill of health.
    uninformative = bool(float(np.mean(lap)) < 0.01 or float(np.std(lap)) < 1e-3)
    return {"n": len(rows), "weeks": len(weeks), "uninformative": uninformative,
            "lap_sd": float(np.std(lap)), "coef": {"a": float(coef[0]), "b_signal": float(coef[1]),
            "c_lap": float(coef[2]), "d_signal_x_lap": float(coef[3])}, "d_ci": [float(lo), float(hi)],
            "leak_signature": bool(lo > 0), "bootstrap_block": block,
            "mean_lap": float(np.mean(lap)) if len(rows) else None,
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
    out = {"tag": tag, "arm": arm, **interaction_test(rows, block=D.bootstrap_block(report))}
    (results / f"lap_test_{tag}_{arm}.json").write_text(json.dumps(out, indent=2, default=float) + "\n")
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
