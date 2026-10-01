"""Anonymised-prompt check (docs/PLAN_60_V2.md "Anonymised-prompt check", spec fixed 2026-10-01): is the Bonsai
judge's backtest edge reading skill, or memory of what happened to these companies?

    python scripts/anon_prompt_check.py score     # GPU: Bonsai scores the masked fact sheets (resumable)
    python scripts/anon_prompt_check.py test      # the one registered comparison, once `score` is complete

`score` asks the judge's own question (BUY or PASS over 5 trading days, log-odds from token probabilities) on each
past release's fact sheet with the ticker, the company's name and the dates masked (app/sandbox/anonymise.py). It
needs Bonsai on Ollama at --base-url (a manual `ollama serve`), like scripts/decide_events.py. `test` compares the
monthly rank IC of the masked and the unmasked log-odds on the same releases.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd

from app.sandbox.anonymise import mask

EV = BACKEND / "results" / "events"
H = 5
# sample -> (the judge's unmasked 5-day file, its events file)
SAMPLES = {"2024": ("decide_bonsai-27b_latest_factsheet2024_secchk_h5.jsonl", "data/events/events_sp500_2024.csv"),
           "2025-26": ("decide_bonsai-27b_latest_factsheet_secchk_h5.jsonl", "data/events/events_sp500_2025.csv")}
OUT = EV / "anon_prompt_h5.jsonl"
TRIAL = "anon_prompt_check"
MIN_MONTH = 20


def _jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def names() -> dict[str, str]:
    m = pd.read_csv(BACKEND / "data" / "events" / "members_2024_2026.csv").drop_duplicates("ticker")
    return dict(zip(m["ticker"], m["name"], strict=True))


def todo(done: set[str]) -> list[dict[str, Any]]:
    """Every uncensored release of both samples without a masked score yet, with its masked prompt."""
    nm = names()
    rows = []
    for sample, (fname, _) in SAMPLES.items():
        for r in _jsonl(EV / fname):
            if not r.get("censored") and r["accession"] not in done:
                rows.append({"accession": r["accession"], "ticker": r["ticker"], "sample": sample,
                             "logodds": r["logodds"], "user": mask(r["prompt_user"], r["ticker"], nm.get(r["ticker"]))})
    return rows


async def score(llm: Any, rows: list[dict[str, Any]], out: Path, parallel: int = 3) -> int:
    from decide_events import DECIDE_SYSTEM
    system = DECIDE_SYSTEM.replace("next 20 trading days", f"next {H} trading days")

    async def one(r: dict[str, Any]) -> dict[str, Any]:
        got = json.loads(await llm(system, r["user"], mode="buypass_lo"))
        return {"accession": r["accession"], "ticker": r["ticker"], "sample": r["sample"],
                "logodds_masked": float(got["logodds"]), "mass": got["mass"], "censored": got["censored"]}
    t0 = time.monotonic()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        for c in range(0, len(rows), parallel):
            for rec in await asyncio.gather(*(one(r) for r in rows[c:c + parallel])):
                f.write(json.dumps(rec) + "\n")
            f.flush()
            n = c + len(rows[c:c + parallel])
            if n // 200 != c // 200:
                rate = (time.monotonic() - t0) / n
                print(f"  {n}/{len(rows)} {rate:.2f}s each, eta {(len(rows) - n) * rate / 60:.0f} min", flush=True)
    return len(rows)


def month_ics(df: pd.DataFrame, col: str) -> pd.Series:
    """Rank IC of `col` against fwd5 per entry month (months with at least MIN_MONTH releases)."""
    ics = {m: g[col].rank().corr(g["fwd5"].rank()) for m, g in df.groupby("month")
           if len(g) >= MIN_MONTH and g[col].nunique() > 1}
    return pd.Series(ics, dtype=float).dropna()


def compare(df: pd.DataFrame, n_boot: int = 5000, seed: int = 0) -> dict[str, Any]:
    """df: accession, sample, month, logodds, logodds_masked, fwd5. The spec's measure and verdict."""
    a, b = month_ics(df, "logodds"), month_ics(df, "logodds_masked")
    months = a.index.intersection(b.index)
    d = (b[months] - a[months]).to_numpy()
    res: dict[str, Any] = {"releases": len(df), "months": len(months),
                           "ic_unmasked": round(float(a[months].mean()), 4) if len(months) else None,
                           "ic_masked": round(float(b[months].mean()), 4) if len(months) else None}
    if len(d) > 1:
        boots = d[np.random.default_rng(seed).integers(0, len(d), (n_boot, len(d)))].mean(1)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        res |= {"d_mean": round(float(d.mean()), 4), "d_ci95": [round(float(lo), 4), round(float(hi), 4)],
                "verdict": "memory flag" if hi < 0 else "no evidence of memory"}
    else:
        res |= {"d_mean": None, "d_ci95": None, "verdict": "not enough months"}
    res["rank_corr_masked_vs_unmasked"] = round(float(df["logodds"].rank().corr(df["logodds_masked"].rank())), 4)
    res["call_flips_pct"] = round(100 * float(((df["logodds"] > 0) != (df["logodds_masked"] > 0)).mean()), 1)
    res["by_sample"] = {s: {"releases": len(g), "ic_unmasked": _mean(month_ics(g, "logodds")),
                            "ic_masked": _mean(month_ics(g, "logodds_masked"))} for s, g in df.groupby("sample")}
    return res


def _mean(s: pd.Series) -> float | None:
    return round(float(s.mean()), 4) if len(s) else None


def table() -> pd.DataFrame:
    """Masked and unmasked log-odds with the 5-day sector-relative result, for every scored release."""
    from event_eval import build

    from app.sandbox.events import Prices
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    masked = pd.DataFrame(_jsonl(OUT))
    masked = masked[~masked["censored"].astype(bool)]
    parts = []
    for sample, (fname, events) in SAMPLES.items():
        un = pd.DataFrame([{"accession": r["accession"], "logodds": r["logodds"]} for r in _jsonl(EV / fname)
                           if not r.get("censored")])
        fwd = build(pd.read_csv(BACKEND / events), p)
        fwd = fwd[fwd["scorable"]][["accession", "month", f"fwd{H}"]].rename(columns={f"fwd{H}": "fwd5"})
        x = un.merge(masked[masked["sample"] == sample][["accession", "logodds_masked"]], on="accession")
        parts.append(x.merge(fwd, on="accession").dropna(subset=["fwd5"]).assign(sample=sample))
    return pd.concat(parts, ignore_index=True)


def run_test() -> dict[str, Any]:
    from app.sandbox.dsr import register
    f = EV / "anon_prompt_check.json"
    if f.exists():
        raise SystemExit(f"{f.name} exists: the check was already run (one run only)")
    left = len(todo({x["accession"] for x in _jsonl(OUT)}))
    if left:
        raise SystemExit(f"{left} releases still without a masked score: finish `score` first")
    res = compare(table())
    f.write_text(json.dumps(res, indent=1) + "\n")
    register({"trial": TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "validity", "window": "2024..2026-09",
              "result": res["verdict"]})
    print(json.dumps(res, indent=1))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("score", "test", "status"))
    ap.add_argument("--base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    done = {x["accession"] for x in _jsonl(OUT)}
    if a.cmd == "status":
        print(json.dumps({"scored": len(done), "to_go": len(todo(done))}))
    elif a.cmd == "test":
        run_test()
    else:
        from app.sandbox.gpu_lock import gpu_job
        from app.sandbox.walkforward import OllamaLLM
        rows = todo(done)
        print(f"{len(rows)} masked fact sheets to score ({len(done)} done)", flush=True)

        async def go() -> None:
            llm = OllamaLLM("bonsai-27b:latest", base_url=a.base_url, concurrency=a.parallel, num_ctx=4096,
                            num_predict=400, cache=True, require_gpu=True)
            try:
                await score(llm, rows, OUT, a.parallel)
            finally:
                await llm.unload()
        with gpu_job("anon_prompt_check bonsai-27b:latest"):
            asyncio.run(go())


if __name__ == "__main__":
    main()
