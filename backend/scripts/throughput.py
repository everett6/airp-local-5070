"""How long decisions take, and how close they come to their open (docs/PLAN_60_V2.md, "Outside review, second
part", stage timings).

    python scripts/throughput.py                      # the live ledger -> results/forward/throughput.json
    python scripts/throughput.py --dir results/rehearsal_busy --out results/rehearsal_busy/throughput.json

From the ledger alone. Per run: the seconds each stage took (the runner records them in its `run` line since
1 Oct 2026). Per release: how long it waited for a run to start (queue wait), how long that run took to write its
decision (work), the two together (latency from the SEC's acceptance), and how much time was left before its open
(slack). Median and worst of each. A decision written with under 10 minutes to spare is an alert: the next busy
morning it will be late.

The seven decisions of 30 Sep and 1 Oct 2026 carry an acceptance time 4 hours early (the SEC list's New York clock
time; see docs/PLAN_60_V2.md, rule 6), so their queue wait and latency read 4 hours too long. Later ones are right.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.forward.ledger import Ledger

TIGHT_S = 600.0


def _utc(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _stats(xs: list[float]) -> dict[str, float] | None:
    return {"median": round(statistics.median(xs), 1), "worst": round(max(xs), 1), "n": len(xs)} if xs else None


def report(recs: list[dict[str, Any]]) -> dict[str, Any]:
    stages: dict[str, list[float]] = {}
    busiest: dict[str, Any] = {}
    for r in recs:
        if r.get("type") == "run" and r.get("timing"):
            for k, v in r["timing"].items():
                stages.setdefault(k, []).append(float(v))
            if int(r.get("new") or 0) >= int(busiest.get("new") or 0):
                busiest = {"as_of": r["as_of"], "new": r.get("new"), "gpu": r.get("gpu"), "timing": r["timing"]}
    wait, work, latency, slack, tight = [], [], [], [], []
    for r in recs:
        if r.get("type") != "decision" or not r.get("written_at"):
            continue
        acc, start, done = _utc(r["accepted_utc"]), _utc(r["as_of"]), _utc(r["written_at"])
        dl = _utc(r["entry_deadline"])
        wait.append((start - acc).total_seconds())
        work.append((done - start).total_seconds())
        latency.append((done - acc).total_seconds())
        slack.append((dl - done).total_seconds())
        if slack[-1] < TIGHT_S:
            tight.append({"ticker": r["ticker"], "accession": r["accession"], "slack_s": round(slack[-1], 1),
                          "written_at": r["written_at"]})
    missed_late = sum(r.get("type") == "missed" and "after the entry open" in str(r.get("reason")) for r in recs)
    return {"runs_timed": len(stages.get("total", [])),
            "stage_seconds": {k: _stats(v) for k, v in sorted(stages.items())},
            "busiest_run": busiest or None,
            "per_decision_seconds": {"queue_wait": _stats(wait), "work": _stats(work), "latency": _stats(latency)},
            "slack_before_open_seconds": ({"median": round(statistics.median(slack), 1), "least": round(min(slack), 1),
                                          "n": len(slack)} if slack else None),
            "tight": tight, "missed_because_late": missed_late}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--out", default="results/forward/throughput.json")
    a = ap.parse_args()
    ledger = BACKEND / a.dir / "ledger.jsonl"
    if not ledger.exists():
        print("throughput: no ledger yet")
        return
    rep = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **report(Ledger(ledger).records())}
    out = BACKEND / a.out
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(rep, indent=1) + "\n")
    tmp.replace(out)
    for k, v in rep["stage_seconds"].items():
        print(f"  stage {k:11} median {v['median']:>7.1f}s  worst {v['worst']:>7.1f}s  ({v['n']} runs)")
    for k, v in rep["per_decision_seconds"].items():
        if v:
            print(f"  {k.replace('_', ' '):17} median {v['median'] / 60:>7.1f} min  worst {v['worst'] / 60:>7.1f} min")
    s = rep["slack_before_open_seconds"]
    if s:
        print(f"  time left before the open: median {s['median'] / 60:.0f} min, least {s['least'] / 60:.0f} min "
              f"({s['n']} decisions); missed because late: {rep['missed_because_late']}")
    for t in rep["tight"]:
        print(f"LEARN ALERT: throughput: {t['ticker']} was decided with {t['slack_s'] / 60:.1f} minutes to spare")


if __name__ == "__main__":
    main()
