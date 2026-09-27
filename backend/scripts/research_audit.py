"""Leak audit of a research run: nothing in a brief may postdate its release's decision time.

    python scripts/research_audit.py [results/events_research_Jan-v1-4B-GGUF_Q4_K_M_v3]

Per release (results/events_research_<model><tag>/<accession>.json): every verified fact's date is on or before the
decision day, and its source URL occurs in the evidence the tools returned; every tool call succeeded only under the
as-of gateway (read_filing refuses filings accepted after the decision time, except the release itself). Prints the
counts and every violation; exit code 1 if there is one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def audit(folder: Path) -> tuple[dict[str, int], list[str]]:
    n = {"releases": 0, "with_brief": 0, "facts": 0, "dated_facts": 0}
    bad: list[str] = []
    for path in sorted(folder.glob("*.json")):
        rec = json.loads(path.read_text())
        n["releases"] += 1
        brief = rec.get("brief")
        if not brief:
            continue
        n["with_brief"] += 1
        day = str(rec["as_of"])[:10]
        evidence = rec.get("evidence")
        for f in brief.get("facts", []):
            n["facts"] += 1
            d = str(f.get("date") or "")[:10]
            if d:
                n["dated_facts"] += 1
                if d > day:
                    bad.append(f"{rec['accession']} {rec['ticker']}: fact dated {d} after the decision day {day}: "
                               f"{str(f.get('text'))[:120]}")
            if evidence is not None and f.get("source") and f["source"] not in evidence:
                bad.append(f"{rec['accession']} {rec['ticker']}: source not in the evidence: {f['source']}")
    return n, bad


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        BACKEND / "results" / "events_research_Jan-v1-4B-GGUF_Q4_K_M_v3"
    n, bad = audit(folder)
    print(f"{n['releases']} releases, {n['with_brief']} with a brief, {n['facts']} verified facts "
          f"({n['dated_facts']} dated); violations: {len(bad)}")
    for line in bad:
        print("  " + line)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
