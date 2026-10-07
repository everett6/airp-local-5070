"""M1: seed results/research_memory/ from every deep-research company file already on disk (oldest first).

    python scripts/research_memory_backfill.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.sandbox import research_memory as rm


def main() -> None:
    files = []
    for f in (BACKEND / "results" / "forward" / "deep_research").glob("*/*.json"):
        if f.name in {"summary.json"}:
            continue
        try:
            row = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and row.get("ticker") and row.get("as_of"):
            files.append((row["as_of"], row))
    files.sort(key=lambda x: x[0])
    for as_of, row in files:
        rm.remember(row, now=datetime.fromisoformat(as_of).astimezone(UTC))
    have = list(rm.ROOT.glob("*.json"))
    print(f"{len(files)} research files folded into {len(have)} company memories; "
          f"{sum(len(json.loads(p.read_text())['facts']) for p in have)} facts")


if __name__ == "__main__":
    main()
