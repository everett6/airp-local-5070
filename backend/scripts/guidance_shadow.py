"""Prospective shadow comparison: verified raised guidance among high-score Bonsai events.

Frozen rule and evaluation are in docs/EXPERIMENT_GUIDANCE_V1.md. Reads forward ledgers only; never
places orders or changes the AI-picks sleeve. Run automatically after forward_events.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.forward.ledger import Ledger
from app.portfolio.sleeve import COST, THRESHOLD

START = date(2026, 9, 30)
ROUND_TRIP_COST = 4 * COST  # two legs, entry and exit, as a fraction of the stock leg's notional
MIN_CONTROL, MIN_RAISED, MIN_MONTHS = 30, 10, 3


def eligible(r: dict[str, Any]) -> bool:
    return (r.get("type") == "decision" and r.get("source") == "bonsai" and r.get("on_time")
            and float(r.get("logodds") or -99) >= THRESHOLD
            and date.fromisoformat(r["accepted_utc"][:10]) >= START)


def collect(events: Path, out: Path) -> dict[str, Any]:
    source = Ledger(events / "ledger.jsonl").verify()
    out.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(out / "ledger.jsonl")
    previous = ledger.verify()
    seen = {r["accession"] for r in previous if r["type"] == "signal"}
    scored = {r["accession"] for r in previous if r["type"] == "outcome"}
    for r in source:
        if eligible(r) and r["accession"] not in seen:
            ledger.append("signal", accession=r["accession"], ticker=r["ticker"],
                          guidance=r.get("guidance", "none"), raised=r.get("guidance") == "raised",
                          logodds=r["logodds"], source_written_at=r["written_at"],
                          entry_deadline=r["entry_deadline"])
            seen.add(r["accession"])
    for r in source:
        if (r.get("type") == "outcome" and r["accession"] in seen and r["accession"] not in scored
                and r.get("fwd5") is not None):
            ledger.append("outcome", accession=r["accession"], entry=r["entry"],
                          excess_gross=r["fwd5"], excess_net=round(float(r["fwd5"]) - ROUND_TRIP_COST, 6))
            scored.add(r["accession"])
    return status(ledger.verify())


def status(recs: list[dict[str, Any]]) -> dict[str, Any]:
    signals = {r["accession"]: r for r in recs if r["type"] == "signal"}
    rows = [{"accession": r["accession"], "entry": r["entry"], "raised": signals[r["accession"]]["raised"],
             "net": float(r["excess_net"])} for r in recs if r["type"] == "outcome" and r["accession"] in signals]
    out: dict[str, Any] = {"signals": len(signals), "matured": len(rows), "raised_matured": 0,
                           "control_mean_net": None, "raised_mean_net": None, "incremental_mean": None,
                           "ci95": None, "verdict": "collecting"}
    if not rows:
        return out
    df = pd.DataFrame(rows)
    raised = df[df["raised"]]
    out["raised_matured"] = len(raised)
    out["control_mean_net"] = round(float(df["net"].mean()), 5)
    if raised.empty:
        return out
    out["raised_mean_net"] = round(float(raised["net"].mean()), 5)
    out["incremental_mean"] = round(out["raised_mean_net"] - out["control_mean_net"], 5)
    months = pd.to_datetime(df["entry"]).dt.to_period("M").nunique()
    if len(df) < MIN_CONTROL or len(raised) < MIN_RAISED or months < MIN_MONTHS:
        return out
    df["week"] = pd.to_datetime(df["entry"]).dt.to_period("W").astype(str)
    weeks = [g for _, g in df.groupby("week")]
    rng = np.random.default_rng(0)
    draws = []
    for ids in rng.integers(0, len(weeks), size=(3000, len(weeks))):
        sample = pd.concat([weeks[i] for i in ids], ignore_index=True)
        selected = sample[sample["raised"]]
        if not selected.empty:
            draws.append(float(selected["net"].mean() - sample["net"].mean()))
    if not draws:
        return out
    lo, hi = np.percentile(draws, [2.5, 97.5])
    out["ci95"] = [round(float(lo), 5), round(float(hi), 5)]
    out["verdict"] = ("promising_shadow_only" if lo > 0 and out["incremental_mean"] >= 0.005
                      else "not_supported" if hi < 0 else "inconclusive")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", default="results/forward/events")
    ap.add_argument("--out", default="results/forward/guidance_shadow")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    out = BACKEND / args.out
    result = (status(Ledger(out / "ledger.jsonl").verify()) if args.status
              else collect(BACKEND / args.events, out))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
