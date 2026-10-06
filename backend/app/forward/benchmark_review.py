"""Source-bound human attestations. Never treat the model's original labels as human verified."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import math
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.forward.ledger import Ledger


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def case_material(backend: Path, case: dict[str, Any]) -> dict[str, Any]:
    accession = case["accession"]
    if not isinstance(accession, str) or not all(c.isdigit() or c == "-" for c in accession):
        raise ValueError("Invalid benchmark accession")
    source = backend / "data" / "events" / "text" / f"{accession}.txt.gz"
    text = ""
    if source.exists():
        with gzip.open(source, "rt") as stream:
            text = stream.read(2_000_001)
        if len(text) > 2_000_000:
            raise ValueError("Benchmark source exceeds review size limit")
    excerpts = []
    for field in ("revenue", "eps", "adj_eps"):
        for slot, value in case.get(field, {}).items():
            if slot not in ("q", "prior") or not isinstance(value, int | float):
                continue
            forms = {str(value), f"{value:,}", f"{value:,.2f}"}
            hits = list(re.finditer(r"(?<![\d.,])(?:" + "|".join(re.escape(s) for s in forms) + r")(?!\d|\.\d|,\d)", text))
            excerpts.append({"field": f"{field}.{slot}", "label": value, "occurrences": len(hits),
                "snippets": [text[max(0, m.start() - 350):m.end() + 350] for m in hits[:3]],
                "note": "Candidate matches only. Verify table headers, period, units and basis in the full source."})
    return {"case": case, "case_hash": digest(case), "source_hash": hashlib.sha256(text.encode()).hexdigest() if text else None,
            "source": text, "source_available": bool(text), "label_excerpts": excerpts}


def materials(backend: Path) -> list[dict[str, Any]]:
    gold = json.loads((backend / "benchmarks" / "extraction" / "gold.json").read_text())
    p = backend / "benchmarks" / "extraction" / "reviews.jsonl"
    reviews = Ledger(p).verify() if p.exists() else []
    latest = {r["accession"]: r for r in reviews}
    out = []
    for case in gold["cases"]:
        item = case_material(backend, case)
        review = latest.get(case["accession"])
        valid = bool(review and item["source_available"] and review.get("source_hash") == item["source_hash"]
                     and review.get("case_hash") == item["case_hash"])
        item["review"] = review if valid else None
        item["approved"] = bool(valid and review and review["type"] == "approved")
        item["needs_review"] = not item["approved"]
        out.append(item)
    return out


def attest(backend: Path, accession: str, reviewer: str, checks: list[str], decision: str,
           note: str, expected_source_hash: str, expected_case_hash: str,
           corrected: dict[str, Any] | None = None) -> None:
    required = {"period", "units", "basis", "absence"}
    if not reviewer.strip() or not note.strip() or set(checks) != required or decision not in ("approved", "rejected"):
        raise ValueError("A reviewer, note, all four checks and an explicit decision are required")
    item = next((m for m in materials(backend) if m["case"]["accession"] == accession), None)
    if (item is None or not item["source_available"] or item["source_hash"] != expected_source_hash
            or item["case_hash"] != expected_case_hash):
        raise ValueError("Source missing or changed; reopen the review")
    corrected = item["case"] if corrected is None else corrected
    if not isinstance(corrected, dict) or set(corrected) != set(item["case"]):
        raise ValueError("Corrections must retain the labelled fields; use null for absent information")
    if corrected.get("accession") != accession or corrected.get("ticker") != item["case"]["ticker"]:
        raise ValueError("Correction cannot change case identity")
    if "period_end" in corrected:
        date.fromisoformat(corrected["period_end"])
    if "categories" in corrected and (not isinstance(corrected["categories"], list)
                                      or not all(isinstance(c, str) for c in corrected["categories"])):
        raise ValueError("Categories must be a list of names")
    if any(not isinstance(corrected[k], str) for k in ("notes", "guidance") if k in corrected):
        raise ValueError("Notes and guidance must remain text")
    for field in ("revenue", "eps", "adj_eps"):
        if field in corrected:
            if not isinstance(corrected[field], dict) or set(corrected[field]) != set(item["case"][field]):
                raise ValueError("Corrections must retain the labelled slots")
            for slot in ("q", "prior"):
                v = corrected[field].get(slot)
                if v is not None and (isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)):
                    raise ValueError("Corrected labels must be numbers or null")
            group = corrected[field]
            for key in ("traps", "other_basis"):
                if key not in group:
                    continue
                if not isinstance(group[key], dict):
                    raise TypeError("Trap and accounting-basis comparisons must be objects")
                comparisons = list(group[key].values()) if key == "traps" else [
                    value for comparison in group[key].values() for value in comparison.values()
                    ] if all(isinstance(c, dict) for c in group[key].values()) else [False]
                if any((v is None and key == "traps") or (v is not None and (
                        isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v))) for v in comparisons):
                    raise ValueError("Trap and accounting-basis values must be finite numbers or null")
    review_path = backend / "benchmarks" / "extraction" / "reviews.jsonl"
    with review_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        Ledger(review_path).verify()
        Ledger(review_path).append(decision,
            accession=accession, reviewer=reviewer.strip()[:100], reviewer_kind="human_attested",
            checks=sorted(required), note=note.strip()[:2000], at=datetime.now(UTC).isoformat(),
            source_hash=item["source_hash"], case_hash=item["case_hash"], corrected_case=corrected)
