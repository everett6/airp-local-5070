"""Fact sheet v2 against v1 on the past releases (docs/PLAN_60_V2.md, "Fact sheet v2"): one registered trial.

    python scripts/factsheet_v2_check.py build     # the v2 fact sheets of the 3,175 sample releases (CPU)
    python scripts/factsheet_v2_check.py score     # the judge on them (GPU; unchanged sheets replay from the cache)
    python scripts/factsheet_v2_check.py status
    python scripts/factsheet_v2_check.py test      # the verdict: once, run by Claude only (it calls register())

v2 reconciles the reader's year-earlier revenue and EPS with the SEC-filed quarters (app/sandbox/reconcile.py). It
replaces the live fact sheet only if its figures are truer (against the comparative each company later filed for
that quarter) and the judge's rank IC is no worse (95% interval of the monthly difference above -0.01).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from anon_prompt_check import MIN_MONTH, H, month_ics

from app.forward.ledger import jsonl_records

EV = BACKEND / "results" / "events"
PY = str(BACKEND / ".venv" / "bin" / "python")
TRIAL, MARGIN, REPLACED_RIGHT = "factsheet_v2_reconciled", -0.01, 0.80
# sample: events file, v1 features, v1 judge file, v2 name, v2 judge tag, extra judge arguments
SAMPLES: dict[str, dict[str, Any]] = {
    "2024": {"events": "data/events/events_sp500_2024.csv", "v1": "features_sp500_2024_secchk.csv",
             "judge_v1": "decide_bonsai-27b_latest_factsheet2024_secchk_h5.jsonl", "name": "sp500_2024_v2",
             "tag": "factsheet2024_v2", "args": ["--from", "2024-01-01", "--to", "2024-12-31"]},
    "2025-26": {"events": "data/events/events_sp500_2025.csv", "v1": "features_sp500_2025_secchk.csv",
                "judge_v1": "decide_bonsai-27b_latest_factsheet_secchk_h5.jsonl", "name": "sp500_2025_v2",
                "tag": "factsheet_v2", "args": []},
}
OUT = EV / "factsheet_v2_check.json"


def v2_features(s: dict[str, Any]) -> Path:
    return EV / f"features_{s['name']}.csv"


def v2_judge(s: dict[str, Any]) -> Path:
    return EV / f"decide_bonsai-27b_latest_{s['tag']}_h{H}.jsonl"


def build() -> None:
    for s in SAMPLES.values():
        subprocess.run([PY, "scripts/build_features.py", "--events", s["events"], "--name", s["name"],
                        "--sheet-version", "2"], cwd=BACKEND, check=True)


def score(base_url: str, parallel: int) -> None:
    for s in SAMPLES.values():
        subprocess.run([PY, "scripts/decide_events.py", "--events", s["events"], *s["args"], "--features",
                        str(v2_features(s).relative_to(BACKEND)), "--tag", s["tag"], "--horizon", str(H),
                        "--explain", "0", "--base-url", base_url, "--parallel", str(parallel)], cwd=BACKEND, check=True)


def status() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, s in SAMPLES.items():
        f = v2_features(s)
        n = len(pd.read_csv(f)) if f.exists() else 0
        out[k] = {"v2_sheets": n, "judged": len(jsonl_records(v2_judge(s))), "to_judge": len(jsonl_records(EV / s["judge_v1"]))}
    return out


def later_comparative(xb: pd.DataFrame, released: str) -> dict[str, float | None]:
    """The year-earlier revenue and EPS the company itself filed later for the release's own quarter: the first
    quarter after the latest one filed before the release (60-125 days later), filed on or after the release day."""
    none: dict[str, float | None] = {"rev": None, "eps": None}
    before = xb[xb["filed"] < released[:10]].sort_values("end")
    if before.empty:
        return none
    gap = (pd.to_datetime(xb["end"]) - pd.Timestamp(before["end"].iloc[-1])).dt.days
    own = xb[(gap >= 60) & (gap <= 125) & (xb["filed"] >= released[:10])].sort_values("end")
    if own.empty:
        return none
    r = own.iloc[0]
    return {"rev": None if pd.isna(r["rev_prior"]) else float(r["rev_prior"]),
            "eps": None if pd.isna(r["eps_prior"]) else float(r["eps_prior"])}


def matches(key: str, shown: float, filed: float) -> bool:
    return (filed != 0 and abs(shown / filed - 1) <= 0.01) if key == "rev" else abs(shown - filed) <= 0.01


def truth(v1: pd.DataFrame, v2: pd.DataFrame, xb: pd.DataFrame) -> dict[str, Any]:
    """Share of fact sheets whose year-earlier figure matches the later-filed comparative, v1 and v2, per figure;
    and the same share among the figures v2 replaced (rule 2)."""
    by_cik = {int(c): g for c, g in xb.groupby("cik")}
    m = v1.merge(v2, on="accession", suffixes=("_1", "_2"))
    res: dict[str, Any] = {}
    replaced = {"n": 0, "right": 0}
    for key, col, rec in (("rev", "rev_prior", "rev_reconcile"), ("eps", "eps_prior", "eps_reconcile")):
        n = r1 = r2 = 0
        for r in m.to_dict("records"):
            a, b, cik = r[f"{col}_1"], r[f"{col}_2"], int(r["cik_1"])
            if pd.isna(a) or pd.isna(b) or cik not in by_cik:
                continue
            filed = later_comparative(by_cik[cik], str(r["accepted_utc_1"]))[key]
            if filed is None:
                continue
            n += 1
            r1 += matches(key, float(a), filed)
            r2 += matches(key, float(b), filed)
            if r[rec] == "previous_quarter":
                replaced["n"] += 1
                replaced["right"] += matches(key, float(b), filed)
        res[key] = {"checked": n, "right_v1": round(r1 / n, 4) if n else None, "right_v2": round(r2 / n, 4) if n else None}
    res["replaced"] = {**replaced, "share_right": round(replaced["right"] / replaced["n"], 4) if replaced["n"] else None}
    return res


def table() -> pd.DataFrame:
    """Both versions' log-odds with the 5-day sector-relative result, for every release scored in both."""
    from event_eval import build

    from app.sandbox.events import Prices
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    parts = []
    for sample, s in SAMPLES.items():
        def lo(f: Path, col: str) -> pd.DataFrame:
            return pd.DataFrame([{"accession": r["accession"], col: r["logodds"], f"sheet_{col}": r["prompt_user"]}
                                 for r in jsonl_records(f) if not r.get("censored")])
        x = lo(EV / s["judge_v1"], "v1").merge(lo(v2_judge(s), "v2"), on="accession")
        fwd = build(pd.read_csv(BACKEND / s["events"]), p)
        fwd = fwd[fwd["scorable"]][["accession", "month", f"fwd{H}"]].rename(columns={f"fwd{H}": "fwd5"})
        parts.append(x.merge(fwd, on="accession").dropna(subset=["fwd5"]).assign(sample=sample))
    return pd.concat(parts, ignore_index=True)


def compare(df: pd.DataFrame, n_boot: int = 5000, seed: int = 0) -> dict[str, Any]:
    """df: accession, sample, month, v1, v2, fwd5, sheet_v1, sheet_v2. The registered measure of the judge's side."""
    a, b = month_ics(df, "v1"), month_ics(df, "v2")
    months = a.index.intersection(b.index)
    d = (b[months] - a[months]).to_numpy()
    res: dict[str, Any] = {"releases": len(df), "months": len(months), "min_per_month": MIN_MONTH,
                           "ic_v1": round(float(a[months].mean()), 4), "ic_v2": round(float(b[months].mean()), 4)}
    boots = d[np.random.default_rng(seed).integers(0, len(d), (n_boot, len(d)))].mean(1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    res |= {"d_mean": round(float(d.mean()), 4), "d_ci95": [round(float(lo), 4), round(float(hi), 4)],
            "no_worse": bool(lo > MARGIN)}
    ch = df[df["sheet_v1"] != df["sheet_v2"]]
    res["changed_sheets"] = {"n": len(ch),
                             "ic_v1": None if len(ch) < MIN_MONTH else round(float(ch["v1"].rank().corr(ch["fwd5"].rank())), 4),
                             "ic_v2": None if len(ch) < MIN_MONTH else round(float(ch["v2"].rank().corr(ch["fwd5"].rank())), 4)}
    res["rank_corr_v1_v2"] = round(float(df["v1"].rank().corr(df["v2"].rank())), 4)
    res["call_flips_pct"] = round(100 * float(((df["v1"] > 0) != (df["v2"] > 0)).mean()), 1)
    res["by_sample"] = {s: {"releases": len(g), "ic_v1": round(float(month_ics(g, "v1").mean()), 4),
                            "ic_v2": round(float(month_ics(g, "v2").mean()), 4)} for s, g in df.groupby("sample")}
    return res


def verdict(judge: dict[str, Any], figs: dict[str, Any]) -> dict[str, Any]:
    truer = all(figs[k]["checked"] and figs[k]["right_v2"] >= figs[k]["right_v1"] for k in ("rev", "eps"))
    rep = figs["replaced"]
    replaced_ok = rep["n"] == 0 or rep["share_right"] >= REPLACED_RIGHT
    return {"truer_figures": bool(truer and replaced_ok), "no_worse_for_the_judge": judge["no_worse"],
            "pass": bool(truer and replaced_ok and judge["no_worse"])}


def run_test() -> dict[str, Any]:
    from app.sandbox.dsr import register
    if OUT.exists():
        raise SystemExit(f"{OUT.name} exists: the check was already run (one run only)")
    st = status()
    left = {k: v for k, v in st.items() if v["judged"] < v["to_judge"]}
    if left:
        raise SystemExit(f"not every release is judged on its v2 sheet yet: {left}")
    xb = pd.read_csv(BACKEND / "data/xbrl/eps_quarterly.csv")
    v1 = pd.concat([pd.read_csv(EV / s["v1"]) for s in SAMPLES.values()])
    v2 = pd.concat([pd.read_csv(v2_features(s)) for s in SAMPLES.values()])
    figs = truth(v1, v2, xb)
    judge = compare(table())
    res = {"trial": TRIAL, "figures": figs, "judge": judge,
           "rules": {"revenue": v2["rev_reconcile"].value_counts().to_dict(),
                     "eps": v2["eps_reconcile"].value_counts().to_dict()},
           **verdict(judge, figs)}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    register({"trial": TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "validity", "window": "2024..2026-09",
              "result": "pass" if res["pass"] else "fail"})
    print(json.dumps(res, indent=1))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("build", "score", "status", "test"))
    ap.add_argument("--base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    elif a.cmd == "score":
        score(a.base_url, a.parallel)
    elif a.cmd == "status":
        print(json.dumps(status()))
    else:
        run_test()


if __name__ == "__main__":
    main()
