"""Step 3 of "LLM extracts, code scores" (docs/PLAN_60_V2.md; rule fixed before any extraction existed).

    python scripts/llm_fields.py test

Base = ridge on EPS growth + revenue growth; full = base + Bonsai's 7 verified fields (one-hot). Both are trained on
the 2024 S&P 500 sample only and scored on the 2025-26 sample. Pass: the 2025-26 monthly rank IC of (full - base) has
a 95% CI above 0 (paired monthly bootstrap) AND the full model's own IC CI is above 0 (5-day return vs sector).
Writes results/events/llm_fields_test.json and registers one trial.
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
from bonsai_lite import change, ridge
from event_eval import build
from llm_fields import FIELDS

from app.sandbox.dsr import register
from app.sandbox.events import Prices, monthly_ic

EV = BACKEND / "results" / "events"
SAMPLES = {"2024": ("data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
           "2025": ("data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv")}


def load(tag: str, p: Prices) -> pd.DataFrame:
    events, feats = SAMPLES[tag]
    df = build(pd.read_csv(BACKEND / events), p)
    f = pd.read_csv(EV / feats)[["accession", "eps_q", "eps_prior", "rev_q", "rev_prior"]]
    lab = pd.DataFrame([json.loads(x) for x in (EV / f"llm_fields_{tag}.jsonl").read_text().splitlines()])
    return df[df["scorable"]].merge(f, on="accession").merge(lab[["accession", *FIELDS]], on="accession")


def design(df: pd.DataFrame, full: bool) -> np.ndarray:
    eps, rev = change(df["eps_q"], df["eps_prior"], 2.0), change(df["rev_q"], df["rev_prior"], 1.0)
    cols = [eps.fillna(0.0), eps.isna().astype(float), rev.fillna(0.0), rev.isna().astype(float)]
    if full:
        for f, (labels, default) in FIELDS.items():
            cols += [(df[f] == v).astype(float) for v in labels if v != default]
    return np.column_stack([c.to_numpy(float) for c in cols])


def paired_diff(df: pd.DataFrame, a: str, b: str, outcome: str, n_boot: int = 5000) -> dict[str, float]:
    diffs = []
    for _, g in df.dropna(subset=[outcome]).groupby("month"):
        if len(g) >= 20:
            diffs.append(g[b].rank().corr(g[outcome].rank()) - g[a].rank().corr(g[outcome].rank()))
    x = np.array(diffs)
    boots = x[np.random.default_rng(0).integers(0, len(x), (n_boot, len(x)))].mean(1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"months": len(x), "mean": round(float(x.mean()), 4), "ci_lo": round(float(lo), 4), "ci_hi": round(float(hi), 4)}


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    train, test = load("2024", p), load("2025", p)
    out: dict = {"n_train": len(train), "n_test": len(test)}
    for h in ("fwd5", "fwd20"):
        tr = train.dropna(subset=[h])
        for name, full in (("base", False), ("full", True)):
            model = ridge(design(tr, full), tr[h].to_numpy(float), lam=10.0)
            test[f"{name}_{h}"] = model(design(test, full))
        out[h] = {"base": monthly_ic(test, f"base_{h}", h), "full": monthly_ic(test, f"full_{h}", h),
                  "full_minus_base": paired_diff(test, f"base_{h}", f"full_{h}", h)}
    out["label_counts"] = {f: test[f].value_counts().to_dict() for f in FIELDS}
    r5 = out["fwd5"]
    out["pass"] = bool(r5["full_minus_base"]["ci_lo"] > 0 and (r5["full"]["ci_lo"] or 0) > 0)
    (EV / "llm_fields_test.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    register({"trial": "llm_fields_code_scores", "date": time.strftime("%Y-%m-%d"), "kind": "signal_ic",
              "ic": r5["full"]["mean_ic"], "result": "pass" if out["pass"] else "fail"})
    print(json.dumps({k: v for k, v in out.items() if k != "label_counts"}, indent=1))
    print("verdict (pre-registered, 5-day):", "PASS" if out["pass"] else "FAIL")


if __name__ == "__main__":
    main()


def research_main() -> None:
    """Arm B (docs/PLAN_60_V2.md "Arm B"): Jan's research -> Bonsai's 8 labels -> the same ridge. Pass: the 2025-26
    monthly IC of (full B - full A) has a 95% CI above 0 AND full B's own IC CI is above 0 (5-day)."""
    from llm_fields import FIELDS_R
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    data = {}
    for tag in ("2024", "2025"):
        a = load(tag, p)
        b = pd.DataFrame([json.loads(x) for x in (EV / f"llm_fields_research_{tag}.jsonl").read_text().splitlines()])
        b = b[["accession", "has_research", *FIELDS_R]].rename(columns={f: f"{f}_r" for f in FIELDS_R})
        data[tag] = a.merge(b, on="accession")
    train, test = data["2024"], data["2025"]

    def design_b(df: pd.DataFrame) -> np.ndarray:
        cols = [design(df, False)]
        for f, (labels, default) in FIELDS_R.items():
            cols.append(np.column_stack([(df[f"{f}_r"] == v).astype(float).to_numpy() for v in labels if v != default]))
        return np.column_stack(cols)
    out: dict = {"n_train": len(train), "n_test": len(test),
                 "with_research": {"2024": float(train["has_research"].mean()), "2025": float(test["has_research"].mean())}}
    for h in ("fwd5", "fwd20"):
        tr = train.dropna(subset=[h])
        test[f"A_{h}"] = ridge(design(tr, True), tr[h].to_numpy(float), lam=10.0)(design(test, True))
        test[f"B_{h}"] = ridge(design_b(tr), tr[h].to_numpy(float), lam=10.0)(design_b(test))
        g = test["vs_prior_guidance_r"].map({"beat": 1.0, "met": 0.0, "missed": -1.0})
        test[f"guid_{h}"] = g
        out[h] = {"full_A": monthly_ic(test, f"A_{h}", h), "full_B": monthly_ic(test, f"B_{h}", h),
                  "B_minus_A": paired_diff(test, f"A_{h}", f"B_{h}", h),
                  "vs_prior_guidance_alone": monthly_ic(test, f"guid_{h}", h, min_n=10)}
    out["vs_prior_guidance_counts"] = test["vs_prior_guidance_r"].value_counts().to_dict()
    r5 = out["fwd5"]
    out["pass"] = bool(r5["B_minus_A"]["ci_lo"] > 0 and (r5["full_B"]["ci_lo"] or 0) > 0)
    (EV / "llm_fields_research_test.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    register({"trial": "jan_research_bonsai_fields_code", "date": time.strftime("%Y-%m-%d"), "kind": "signal_ic",
              "ic": r5["full_B"]["mean_ic"], "result": "pass" if out["pass"] else "fail"})
    print(json.dumps(out, indent=1, default=str))
    print("verdict (pre-registered, 5-day):", "PASS" if out["pass"] else "FAIL")
