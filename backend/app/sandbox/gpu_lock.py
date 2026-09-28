"""One GPU job at a time, and the forward test first.

2026-09-16: running the Kronos forecaster while Ollama served a walk-forward run made the RTX 5070 fall off
the PCIe bus (NVIDIA Xid 79), which needs a reboot to recover. Every GPU-using entry point in this repo takes
this exclusive lock first; a second job waits (printing why) instead of running concurrently.

Priority (DEV_PLAN_AUTONOMOUS Phase A "GPU safety"): the forward runner, whose decisions can't be made later, raises
a priority flag while it needs the GPU. Every OllamaLLM in another process checks the flag before each request; if it
is up, it lets its in-flight requests finish, unloads its model and waits until the flag is gone. A flag left by a
process that died is ignored.
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


PRIORITY_ENV = "AIRP_GPU_PRIORITY"  # set in the flag holder's process (inherited by its children): they never yield


def priority_path() -> Path:
    return lock_path().with_name(f"airp-gpu-priority-{os.getuid()}.flag")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def priority_wanted(path: Path | None = None) -> bool:
    """True when another process holds the priority flag (a flag whose holder has died does not count)."""
    if os.environ.get(PRIORITY_ENV) == "1":
        return False
    p = path or priority_path()
    try:
        pid = int(p.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return _alive(pid)


@contextmanager
def gpu_priority(name: str, path: Path | None = None) -> Iterator[None]:
    """Hold the priority flag for the duration (other GPU users yield); this process and its children don't yield."""
    p = path or priority_path()
    p.write_text(f"{os.getpid()} {name}\n")
    old = os.environ.get(PRIORITY_ENV)
    os.environ[PRIORITY_ENV] = "1"
    try:
        yield
    finally:
        p.unlink(missing_ok=True)
        if old is None:
            os.environ.pop(PRIORITY_ENV, None)
        else:
            os.environ[PRIORITY_ENV] = old


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
