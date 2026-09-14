"""
Append-only, hash-chained ledger for the forward test.

Each line is a JSON record whose `hash` covers its own content plus the
previous record's hash. Editing, deleting, or reordering any earlier line
breaks every hash after it, which `verify` reports. Pushing the file to a
third party (e.g. GitHub) adds an independent timestamp to the chain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(rec: dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class LedgerError(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def verify(self) -> list[dict[str, Any]]:
        """Return all records, or raise LedgerError at the first broken link."""
        prev = GENESIS
        recs = self.records()
        for i, rec in enumerate(recs):
            if rec.get("seq") != i or rec.get("prev") != prev or rec.get("hash") != _digest(rec):
                raise LedgerError(f"ledger broken at line {i + 1} (seq {rec.get('seq')})")
            prev = rec["hash"]
        return recs

    def append(self, rtype: str, **fields: Any) -> dict[str, Any]:
        recs = self.verify()
        rec: dict[str, Any] = {"seq": len(recs), "type": rtype,
                               "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
                               **fields, "prev": recs[-1]["hash"] if recs else GENESIS}
        rec["hash"] = _digest(rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        return rec
