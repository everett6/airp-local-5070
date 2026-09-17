"""One GPU job at a time.

2026-09-16: running the Kronos forecaster while Ollama served a walk-forward run made the RTX 5070 fall off
the PCIe bus (NVIDIA Xid 79), which needs a reboot to recover. Every GPU-using entry point in this repo takes
this exclusive lock first; a second job waits (printing why) instead of running concurrently.
"""
from __future__ import annotations

import fcntl
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def lock_path() -> Path:
    """Fixed per user, independent of environment variables: a job started from a login shell (XDG_RUNTIME_DIR
    set) and one started from cron or a bare service (unset) must still find the same lock."""
    uid = os.getuid()
    run = Path(f"/run/user/{uid}")
    base = run if run.is_dir() else Path(tempfile.gettempdir())
    return base / f"airp-gpu-{uid}.lock"


@contextmanager
def gpu_job(name: str, path: Path | None = None, poll_s: float = 10.0) -> Iterator[None]:
    p = path or lock_path()
    with p.open("a+") as f:
        waited = False
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not waited:
                    f.seek(0)
                    print(f"[gpu-lock] {name}: waiting for another GPU job ({f.read().strip() or 'unknown'})", flush=True)
                    waited = True
                time.sleep(poll_s)
        f.seek(0)
        f.truncate()
        f.write(f"{name} pid={os.getpid()}\n")
        f.flush()
        try:
            yield
        finally:
            f.seek(0)
            f.truncate()
            fcntl.flock(f, fcntl.LOCK_UN)
