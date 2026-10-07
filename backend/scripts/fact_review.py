"""O1 #30: a human check of the facts Jan saved (results/research_memory/). The app shows a queue of facts with their
source links; the user marks each correct, wrong or unclear. The share marked correct is Jan's verified accuracy
(shown with the model evidence); citation and number checks by code do not prove the reading is right.

    python scripts/fact_review.py queue [--n 20]     # refill the queue with a seeded sample of recent facts
    python scripts/fact_review.py status             # JSON for the app
    python scripts/fact_review.py label <id> correct|wrong|unclear [--note TEXT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.sandbox import research_memory as rm

REVIEW = BACKEND / "results" / "review"
VERDICTS = ("correct", "wrong", "unclear")


def labels(root: Path = REVIEW) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    try:
        for line in (root / "fact_labels.jsonl").read_text().splitlines():
            r = json.loads(line)
            out[r["id"]] = r
    except (OSError, ValueError):
        pass
    return out


def queue(n: int = 20, root: Path = REVIEW, mem: Path = rm.ROOT) -> list[dict[str, object]]:
    done = labels(root)
    pool = []
    for p in sorted(mem.glob("*.json")):
        m = json.loads(p.read_text())
        for f in m.get("facts", [])[:5]:
            fid = hashlib.sha256(f"{m['ticker']}|{f['text']}".encode()).hexdigest()[:16]
            if fid not in done:
                pool.append({"id": fid, "ticker": m["ticker"], "text": f["text"], "source": f.get("source", ""),
                             "date": f.get("date", ""), "first_seen": f.get("first_seen", "")})
    random.Random(datetime.now(UTC).date().isoformat()).shuffle(pool)
    q = pool[:n]
    root.mkdir(parents=True, exist_ok=True)
    (root / "fact_queue.json").write_text(json.dumps(q, indent=1))
    return q


def label(fid: str, verdict: str, note: str = "", root: Path = REVIEW) -> dict[str, object]:
    if verdict not in VERDICTS:
        raise SystemExit(f"verdict must be one of {VERDICTS}")
    q = {x["id"]: x for x in json.loads((root / "fact_queue.json").read_text())}
    if fid not in q:
        raise SystemExit("not in the queue")
    rec = {**q[fid], "verdict": verdict, "note": note[:300], "at": datetime.now(UTC).isoformat()}
    with (root / "fact_labels.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def status(root: Path = REVIEW) -> dict[str, object]:
    done = labels(root)
    try:
        q = json.loads((root / "fact_queue.json").read_text())
    except (OSError, ValueError):
        q = []
    marked = [r for r in done.values() if r["verdict"] in ("correct", "wrong")]
    acc = sum(r["verdict"] == "correct" for r in marked) / len(marked) if marked else None
    return {"queue": [x for x in q if x["id"] not in done], "labelled": len(done), "accuracy": acc,
            "n_scored": len(marked), "recent": sorted(done.values(), key=lambda r: str(r["at"]))[-20:][::-1]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--n", type=int, default=20)
    sub.add_parser("status")
    lb = sub.add_parser("label")
    lb.add_argument("id")
    lb.add_argument("verdict")
    lb.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "queue":
        print(json.dumps(queue(a.n)))
    elif a.cmd == "label":
        print(json.dumps(label(a.id, a.verdict, a.note)))
    else:
        st = status()
        if not st["queue"] and not st["labelled"]:
            queue()
            st = status()
        print(json.dumps(st))


if __name__ == "__main__":
    main()
