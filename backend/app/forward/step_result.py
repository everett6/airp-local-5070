"""Structured outcomes for runners, with a clearly marked compatibility path for older scripts."""
from __future__ import annotations

import hashlib
import json
from typing import Any

PREFIX = "AIRP_RESULT "


def emit(status: str, **details: Any) -> None:
    if status not in ("ok", "warning", "failed", "skipped"):
        raise ValueError("Invalid step status")
    print(PREFIX + json.dumps({"version": 1, "status": status, **details}), flush=True)


def outcome(command: list[str], rc: int, stdout: str, stderr: str, seconds: float) -> dict[str, Any]:
    native = None
    for line in stdout.splitlines():
        if line.startswith(PREFIX):
            try:
                candidate = json.loads(line[len(PREFIX):])
                if candidate.get("version") == 1 and candidate.get("status") in ("ok", "warning", "failed", "skipped"):
                    native = candidate
            except (ValueError, AttributeError):
                pass
    return {"command": command, "rc": rc, "seconds": seconds,
            "status": "failed" if rc else native["status"] if native else "unknown",
            "protocol": "native" if native else "legacy",
            "details": native,
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest()}
