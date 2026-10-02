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
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

GENESIS = "0" * 64


def _digest(rec: dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class LedgerError(RuntimeError):
    pass


def write_atomic(path: Path, text: str) -> None:
    """Replace a state file in one step (temporary file, then rename): a crash or power loss mid-write leaves the
    old file, never half of the new one. For files a later run must be able to read (books, orders)."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())  # on disk before the rename: a power loss must not leave an empty new file
    tmp.replace(path)


def jsonl_records(path: Path, must_exist: bool = False) -> list[dict[str, Any]]:
    """The JSON objects of a plain (not hash-chained) .jsonl file, skipping a line cut off by a crash or power loss.
    For the pipeline's and the shadows' line files: one torn line must not stop every later run. `must_exist` keeps
    a missing input file an error."""
    out: list[dict[str, Any]] = []
    if not path.exists():
        if must_exist:
            raise FileNotFoundError(path)
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def open_append(path: Path) -> TextIO:
    """Open a .jsonl file for appending. If its last line was cut off (no newline at the end), end that line first,
    so the next record starts on a line of its own and `jsonl_records` skips only the torn one."""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as r:
            r.seek(-1, 2)
            torn = r.read(1) != b"\n"
        if torn:
            with path.open("ab") as w:
                w.write(b"\n")
    return path.open("a")


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> list[dict[str, Any]]:
        """Every whole record. A line that is not a whole JSON object (cut off by a crash or power loss) was never
        a record and is skipped; before 1 Oct 2026 it made every later run fail until the line was removed by hand.
        Skipping it cannot hide anything: each whole record carries its place in the chain, so a record that was
        removed or damaged still fails `verify`."""
        return jsonl_records(self.path)

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
        with open_append(self.path) as f:  # after a cut-off line the new record starts on a line of its own
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())  # a record that was reported as written survives a power loss
        return rec
