"""The consensus shadow (docs/PLAN_60_V2.md "Consensus shadow"): the research agents' votes on each live release,
combined with equal weights and with weights from each agent's own matured record. No money, no GPU.

    python scripts/consensus_shadow.py --dir results/forward/events      # record new releases, then print the score
    python scripts/consensus_shadow.py --dir results/forward/events --status

One line per release goes to <out>/ledger.jsonl, once, never rewritten. A line written at or after the release's
entry deadline is kept but never scored. The event ledger and the agents' files are only read. Runs after each event
run (scripts/autorun.py post_run); a failure here cannot change that run's result.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.forward.ledger import jsonl_records as _jsonl
from app.forward.ledger import open_append
from app.sandbox import consensus as C

# agent -> (file next to the event ledger, its scored field)
READS = {"net_read": ("net_read.jsonl", "net_read"), "ai_read": ("ai_lens.jsonl", "ai_read"),
         "bb_read": ("bull_bear.jsonl", "bb_read")}
PASS = {"min_events": 150, "min_months": 3, "min_ic": 0.02}  # arms C2, D and E's rule, plus the blend-gain test


def _ts(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def on_time_reads(d: Path) -> dict[str, dict[str, str]]:
    """accession -> {agent: label}, labels written before the entry deadline only (the first one per release)."""
    out: dict[str, dict[str, str]] = {}
    for agent, (fname, key) in READS.items():
        for x in _jsonl(d / fname):
            if key in x and _ts(x["written_at"]) < _ts(x["entry_deadline"]):
                out.setdefault(x["accession"], {}).setdefault(agent, x[key])
    return out


def collect(d: Path, out: Path, now: datetime | None = None) -> int:
    """Append a consensus line for every on-time decision that has none yet. Returns how many were added."""
    now = now or datetime.now(UTC)
    recs = _jsonl(d / "ledger.jsonl")
    decisions = [r for r in recs if r.get("type") == "decision" and r.get("on_time")]
    outcomes = {r["accession"]: r for r in recs if r.get("type") == "outcome" and r.get("fwd5") is not None}
    reads = on_time_reads(d)
    path = out / "ledger.jsonl"
    mine = _jsonl(path)
    have = {x["accession"] for x in mine}
    votes_of = {x["accession"]: x["votes"] for x in mine}
    added = 0
    for r in sorted(decisions, key=lambda x: (x["entry_deadline"], x["seq"])):
        acc = r["accession"]
        if acc in have:
            continue
        deadline = _ts(r["entry_deadline"])
        v = C.votes(r.get("logodds"), r.get("guidance"), reads.get(acc, {}))
        # an agent's record: earlier releases whose outcome line was written before this release's entry deadline
        past = [(votes_of[a], float(o["fwd5"])) for a, o in outcomes.items()
                if a in votes_of and _ts(o["written_at"]) < deadline]
        rec = C.records(past)
        line = {"accession": acc, "ticker": r["ticker"], "entry_deadline": r["entry_deadline"], "votes": v,
                "records": {a: list(rec[a]) for a in C.AGENTS}, "matured": len(past), **C.combine(v, rec),
                "written_at": now.isoformat(timespec="seconds")}
        out.mkdir(parents=True, exist_ok=True)
        with open_append(path) as f:
            f.write(json.dumps(line) + "\n")
        have.add(acc)
        votes_of[acc] = v
        added += 1
    return added


def scored(d: Path, out: Path) -> pd.DataFrame:
    """On-time consensus lines joined with matured outcomes: accession, month, eq, rw, logodds, fwd5."""
    cols = ["accession", "month", "eq", "rw", "logodds", "fwd5"]
    recs = _jsonl(d / "ledger.jsonl")
    outc = {r["accession"]: r for r in recs if r.get("type") == "outcome" and r.get("fwd5") is not None}
    lo = {r["accession"]: r.get("logodds") for r in recs if r.get("type") == "decision"}
    rows = [{"accession": x["accession"], "month": outc[x["accession"]]["entry"][:7], "eq": x["eq"], "rw": x["rw"],
             "logodds": lo.get(x["accession"]), "fwd5": outc[x["accession"]]["fwd5"]}
            for x in _jsonl(out / "ledger.jsonl")
            if x["accession"] in outc and _ts(x["written_at"]) < _ts(x["entry_deadline"])]
    return pd.DataFrame(rows, columns=cols)


def _ic(df: pd.DataFrame, col: str) -> dict[str, Any]:
    raw = [g[col].rank().corr(g["fwd5"].rank()) for _, g in df.groupby("month")
           if len(g) >= 10 and g[col].nunique() > 1]
    ics = np.array([x for x in raw if not np.isnan(x)])
    lo80 = None
    if len(ics) > 1:
        b = ics[np.random.default_rng(0).integers(0, len(ics), (5000, len(ics)))].mean(1)
        lo80 = round(float(np.percentile(b, 20)), 4)
    return {"months": len(ics), "mean_ic": round(float(ics.mean()), 4) if len(ics) else None, "ic_lo80": lo80}


def status(d: Path, out: Path) -> dict[str, Any]:
    df = scored(d, out)
    lines = _jsonl(out / "ledger.jsonl")
    last = lines[-1] if lines else None
    rep: dict[str, Any] = {"recorded": len(lines), "scored": len(df)}
    for col in ("eq", "rw"):
        ic = _ic(df, col)
        rep[f"consensus_{col}"] = {**ic, "ready_to_judge": len(df) >= PASS["min_events"]
                                   and ic["months"] >= PASS["min_months"]}
    if last:  # each agent's record and weight as of the newest release
        rep["agents"] = {a: {"hits": last["records"][a][0], "calls": last["records"][a][1],
                             "weight": last["weights"][a]} for a in C.AGENTS}
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--out", default="results/forward/consensus")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    d, out = BACKEND / a.dir, BACKEND / a.out
    if not a.status:
        print(f"consensus shadow: {collect(d, out)} new releases recorded")
    print("consensus (shadow):", json.dumps(status(d, out)))


if __name__ == "__main__":
    main()
