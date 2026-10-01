"""Process-failure log and review (docs/EXECUTIVE_PLAN.md, "failure log and weekly review"). Run by hand.

    python scripts/failure_review.py                       # every run folder it knows
    python scripts/failure_review.py results/spike_briefs  # just these folders

Collects failures that the runs already record, judged without any stock outcome (so learning from them cannot leak
the future):
  research / spike records   tool calls that failed (by tool and cause), unparseable model replies, brief facts the
                             source check dropped (no source, unknown source, a number not in the cited source),
                             records that ended in an error
  forward ledgers            decisions logged as missed, by reason
Rebuilds results/failures.jsonl (one row per failure) and appends a summary to results/failures_summary.jsonl, so each
review shows the change since the previous one.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.forward.ledger import Ledger

RES = BACKEND / "results"
DEFAULT = ["events_research_Jan-v1-4B-GGUF_Q4_K_M_v3", "spike_briefs"]
LEDGERS = ["forward/events", "forward/events_breadth"]


def cause(err: str) -> str:
    """A short, stable cause label: numbers, tickers and URLs removed so the same failure groups together."""
    e = re.sub(r"https?://\S+", "<url>", err or "")
    e = re.sub(r"\b[A-Z]{1,5}\b(?= in the| not found)", "<ticker>", e)
    e = re.sub(r"\d+(\.\d+)?", "#", e)
    return e[:60].strip() or "unknown"


def from_records(folder: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    run = folder.name
    for p in sorted(folder.glob("*.json")):
        r = json.loads(p.read_text())
        rid = p.stem
        for t in r.get("tool_log", []):
            if not t.get("ok"):
                rows.append({"run": run, "id": rid, "stage": f"tool:{t.get('tool') or '?'}",
                             "cause": cause(str(t.get("error", "")))})
        for _ in range(int(r.get("parse_failures") or 0)):
            rows.append({"run": run, "id": rid, "stage": "model_reply", "cause": "unparseable reply"})
        for k, n in ((r.get("brief") or {}).get("dropped") or {}).items():
            rows += [{"run": run, "id": rid, "stage": "brief_check", "cause": k}] * int(n)
        if r.get("error"):
            rows.append({"run": run, "id": rid, "stage": "record", "cause": cause(str(r["error"]))})
    return rows


def from_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    run = path.parent.name
    return [{"run": run, "id": r["accession"], "stage": "forward", "cause": r["reason"]}
            for r in Ledger(path).records() if r.get("type") == "missed"]


def main() -> None:
    folders = sys.argv[1:] or DEFAULT
    rows: list[dict[str, Any]] = []
    records: dict[str, int] = {}
    for f in folders:
        d = (RES / f) if not Path(f).is_absolute() and not f.startswith("results/") else BACKEND / f
        if d.is_dir():
            rows += from_records(d)
            records[d.name] = len(list(d.glob("*.json")))
    for led in LEDGERS:
        rows += from_ledger(RES / led / "ledger.jsonl")
    with (RES / "failures.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    by = collections.Counter((r["run"], r["stage"], r["cause"]) for r in rows)
    summary = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "records": records, "failures": len(rows),
               "top": [{"run": k[0], "stage": k[1], "cause": k[2], "n": n} for k, n in by.most_common(15)]}
    hist = RES / "failures_summary.jsonl"
    prev = json.loads(hist.read_text().splitlines()[-1]) if hist.exists() and hist.read_text().strip() else None
    with hist.open("a") as fh:
        fh.write(json.dumps(summary) + "\n")
    print(f"{len(rows)} failures across {records}")
    old = {(t["run"], t["stage"], t["cause"]): t["n"] for t in (prev or {}).get("top", [])}
    for t in summary["top"]:
        k = (t["run"], t["stage"], t["cause"])
        delta = f" ({t['n'] - old[k]:+d} since last review)" if k in old else ""
        per = f", {t['n'] / records[t['run']]:.2f} per record" if t["run"] in records else ""
        print(f"  {t['n']:5d}  {t['run'][:28]:28s} {t['stage']:22s} {t['cause']}{per}{delta}")


if __name__ == "__main__":
    main()
