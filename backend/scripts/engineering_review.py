"""Explicit local human reviews of releases and supplied broker costs. No activation or order submission."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from ops_tool import sha

from app.forward.ledger import Ledger, write_atomic
from app.portfolio.measurement import cost_scope, execution, recorded_fills


def review(backend: Path, data: dict[str, Any]) -> None:
    action = data.get("action")
    if data.get("confirm") != "REVIEWED" or not str(data.get("reviewer", "")).strip() or not str(data.get("note", "")).strip():
        raise ValueError("A named reviewer, evidence note and REVIEWED confirmation are required")
    audit = backend / "results/forward/execution/ledger.jsonl"
    if action == "costs":
        rows = Ledger(audit).verify()
        digest = cost_scope(backend)
        if not digest or data.get("digest") != digest:
            raise ValueError("Execution records changed or are missing; reload before supplying costs")
        execution(rows, data["costs"], recorded_fills(backend))
        record = {"costs": data["costs"], "cost_scope_sha256": digest,
                  "ledger_sha256": hashlib.sha256(audit.read_bytes()).hexdigest(), "reviewer": data["reviewer"],
                  "source_note": data["note"], "at": datetime.now(UTC).isoformat()}
        folder = audit.parent
        Ledger(folder / "cost_reviews.jsonl").append("cost_input", **record)
        write_atomic(folder / "costs.json", json.dumps(record, allow_nan=False))
    elif action == "release":
        manifests = sorted((backend / "results/ops/releases").glob("*/manifest.json"))
        if not manifests:
            raise ValueError("No candidate to review")
        manifest = manifests[-1]
        meta = json.loads(manifest.read_text())
        index = Ledger(backend / "results/ops/index.jsonl").verify()
        if not any(r.get("id") == meta["id"] and r.get("manifest_sha256") == sha(manifest) for r in index):
            raise ValueError("Candidate digest is not recorded")
        if data.get("digest") != meta["archive_sha256"] or sha(manifest.parent / "snapshot.tar.gz") != data["digest"]:
            raise ValueError("Candidate changed; reload the review")
        if set(data.get("checks", [])) != {"source", "tests", "risk", "recovery"}:
            raise ValueError("Review source, test evidence, risk controls and recovery before approval")
        Ledger(backend / "results/ops/reviews.jsonl").append("human_review", candidate=meta["id"],
            archive_sha256=data["digest"], reviewer=data["reviewer"], note=data["note"], checks=data["checks"],
            at=datetime.now(UTC).isoformat(), activation="separate user decision", identity="local human attestation")
    else:
        raise ValueError("Unknown review action")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", required=True)
    args = ap.parse_args()
    lock = BACKEND / "results/forward/autorun.lock"
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        review(BACKEND, json.loads(args.record))
    print(json.dumps({"saved": True}))


if __name__ == "__main__":
    main()
