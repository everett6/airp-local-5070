"""A1 "Bonsai-lite": a ridge regression (one matrix solve) that copies Bonsai's 1-week log-odds, and a triage rule.

    python scripts/bonsai_lite.py

Spec and pass rules were written before the first run (docs/STRATEGY_RESEARCH.md, "A1"). Features per release, all
known at the decision time: EPS change vs a year earlier, revenue growth (+ missing flags), guidance and tone one-hot,
12-1 month momentum vs the sector ETF (0 when there is not a year of prices), sector one-hot. Walk-forward: each month
of the 2025-26 sample is predicted by a fit on 2024 plus the earlier 2025-26 months (Bonsai's outputs only, never
returns). Arms on the same releases, monthly rank IC on the 5-day excess return: (a) Bonsai, (b) lite alone,
(c) triage = Bonsai only for the month's top half by lite score. Writes results/events/bonsai_lite.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import build
from secchk_eval import load

from app.sandbox.events import Prices, monthly_ic

EV = BACKEND / "results" / "events"
SAMPLES = {"2024": ("factsheet2024_secchk", "data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
           "2025-26": ("factsheet_secchk", "data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv")}
H = 5


def change(q: pd.Series, prior: pd.Series, clip: float) -> pd.Series:
    return ((q - prior) / prior.abs().where(prior != 0)).clip(-clip, clip)


def design(df: pd.DataFrame, cats: dict[str, list[str]]) -> np.ndarray:
    eps = change(df["eps_q"], df["eps_prior"], 2.0)
    rev = change(df["rev_q"], df["rev_prior"], 1.0)
    cols = [eps.fillna(0.0), eps.isna().astype(float), rev.fillna(0.0), rev.isna().astype(float),
            df["momentum"].clip(-1, 1).fillna(0.0)]
    for col, values in cats.items():
        cols += [(df[col] == v).astype(float) for v in values]
    return np.column_stack([c.to_numpy(float) for c in cols])


def ridge(X: np.ndarray, y: np.ndarray, lam: float = 1.0):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    pen = lam * np.eye(Z.shape[1])
    pen[0, 0] = 0.0  # no penalty on the intercept
    beta = np.linalg.solve(Z.T @ Z + pen, Z.T @ y)
    return lambda Xn: np.column_stack([np.ones(len(Xn)), (Xn - mu) / sd]) @ beta


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    frames = []
    for name, (tag, events, feats) in SAMPLES.items():
        df = build(pd.read_csv(BACKEND / events), p)
        f = pd.read_csv(EV / feats)[["accession", "eps_q", "eps_prior", "rev_q", "rev_prior", "guidance", "tone"]]
        df = df[df["scorable"]].merge(f, on="accession").merge(load(tag, H), on="accession")
        frames.append(df.assign(sample=name))
    data = pd.concat(frames, ignore_index=True)
    cats = {c: sorted(data[c].dropna().astype(str).unique()) for c in ("guidance", "tone", "sector")}
    X = design(data, cats)
    y = data["logodds"].to_numpy(float)

    test = data["sample"] == "2025-26"
    data["lite"] = np.nan
    for m in sorted(data.loc[test, "month"].unique()):
        train = (data["sample"] == "2024") | (test & (data["month"] < m))
        now = test & (data["month"] == m)
        data.loc[now, "lite"] = ridge(X[train.to_numpy()], y[train.to_numpy()])(X[now.to_numpy()])
    t = data[test].copy()
    t["top_half"] = t.groupby("month")["lite"].rank(pct=True) > 0.5
    # triage: called releases keep Bonsai's log-odds and rank above every uncalled one
    t["triage"] = np.where(t["top_half"], 1e3 + t["logodds"], t["lite"] - 1e3)

    out = {"n": len(t), "features": X.shape[1], "categories": cats,
           "copy_rank_corr": round(float(t["lite"].rank().corr(t["logodds"].rank())), 3),
           "bonsai_calls_saved_pct": round(100 * float(1 - t["top_half"].mean()), 1)}
    arms = {"a_bonsai": "logodds", "b_lite": "lite", "c_triage": "triage"}
    for arm, col in arms.items():
        out[arm] = monthly_ic(t, col, f"fwd{H}", min_n=10)
    a, b, c = out["a_bonsai"], out["b_lite"], out["c_triage"]
    out["lite_replaces_pass"] = bool(b["mean_ic"] >= a["mean_ic"] - 0.01 and b["ci_lo"] > 0)
    out["triage_pass"] = bool(c["mean_ic"] >= a["mean_ic"] - 0.01 and c["ci_lo"] > 0)
    (EV / "bonsai_lite.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"n={out['n']} releases, {out['features']} features; lite vs Bonsai rank corr {out['copy_rank_corr']}; "
          f"triage saves {out['bonsai_calls_saved_pct']}% of Bonsai calls")
    for arm in arms:
        v = out[arm]
        print(f"  {arm:9s} IC {v['mean_ic']:+.3f} [{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}] months={v['months']}")
    print(f"  lite replaces Bonsai: {'PASS' if out['lite_replaces_pass'] else 'FAIL'};  "
          f"triage: {'PASS' if out['triage_pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
