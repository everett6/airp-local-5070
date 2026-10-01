"""Research queue: keeps long research jobs going without Claude (paper research only; never trades).

    python scripts/research_queue.py tick      # start/restart what is due (called by autorun after each job)
    python scripts/research_queue.py status    # JSON status of every job (used by the desktop app)
    python scripts/research_queue.py pause|resume|approve <name>
    python scripts/research_queue.py init      # write the default queue (once)

No new timers: autorun calls `tick` after the events runs (~05:48, ~15:33) and the 18:00 check. Rules:
- a job already running (matched by its command line) is left alone; a stopped, unfinished job is restarted and
  resumes from its own saved progress (every queued command must be resumable);
- `net` jobs (downloads) may run at any time;
- `gpu` jobs start only between 18:00 and 04:30, need the GPU free of other model servers and 14 GB of free RAM, and
  are stopped at 05:25 by `timeout` (before the 05:45 live run), then resume at the next evening tick;
- a `gate` job never runs anything: once its requirements are done it notifies once and waits for Claude/the user to
  mark it approved (`approve <name>`), because a pre-registered gate needs a human-judged check.
Jobs live in results/research/queue.json; per-job logs in results/research/logs/.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
QDIR = BACKEND / "results" / "research"
QUEUE = QDIR / "queue.json"
GPU_START, GPU_LAST_START, GPU_STOP = (18, 0), (4, 30), (5, 25)
MIN_FREE_RAM_GB = 14.0
LOCAL = ZoneInfo("America/Los_Angeles")  # the live runs' clock (05:45 / 15:30 PDT)


PY = ".venv/bin/python"
DEFAULT_JOBS: list[dict[str, Any]] = [  # B4b (docs/PLAN_60_V2.md "Arm B4b"); later steps are added after its gates
    {"name": "b4b_gdelt_warmup", "kind": "net", "cmd": [PY, "-u", "scripts/warm_gdelt.py"], "match": "warm_gdelt.py",
     "done": {"file": "results/events/warm_gdelt.jsonl", "target": 2851, "status_in": ["ok", "none"]},
     "note": "GDELT headlines for 2,851 releases, one request per 20 s or slower"},
    {"name": "b4b_armA_labels", "kind": "gpu",
     "cmd": ["scripts/bonsai_job.sh", PY, "scripts/llm_fields.py", "extract-b4"], "match": "extract-b4",
     "done": {"file": "results/events/llm_fields_b4.jsonl", "target": 2851},
     "note": "Bonsai P2 on the release alone (arm A)"},
    {"name": "b4b_gates", "kind": "gate", "requires": ["b4b_gdelt_warmup", "b4b_armA_labels"],
     "note": "B4b gates ready: coverage >= 50% and Claude's 40-title relevance check (>= 70%) before Jan + B2"},
]


def load() -> list[dict[str, Any]]:
    return json.loads(QUEUE.read_text()) if QUEUE.exists() else []


def save(jobs: list[dict[str, Any]]) -> None:
    QDIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, indent=1) + "\n")
    tmp.replace(QUEUE)


def running(match: str) -> list[int]:
    """PIDs whose command line contains `match` (reads /proc directly: never matches a grep of itself)."""
    pids = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if match in cmd and "research_queue.py" not in cmd:
            pids.append(int(p.name))
    return pids


def progress(check: dict[str, Any]) -> tuple[int, int]:
    """(done, target) from a job's done-check: distinct keys in a jsonl file, optionally with a status filter."""
    f = BACKEND / check["file"]
    keys: set[str] = set()
    if f.exists():
        for line in f.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "status_in" in check and rec.get("status") not in check["status_in"]:
                continue
            keys.add(str(rec.get(check.get("key", "accession"))))
    return len(keys), int(check["target"])


def is_done(job: dict[str, Any]) -> bool:
    if job.get("kind") == "gate":
        return bool(job.get("approved"))
    d, t = progress(job["done"])
    return d >= t


def gpu_window(now: datetime) -> bool:
    t = (now.hour, now.minute)
    return t >= GPU_START or t < GPU_LAST_START


def seconds_to_stop(now: datetime) -> int:
    stop = now.replace(hour=GPU_STOP[0], minute=GPU_STOP[1], second=0, microsecond=0)
    if stop <= now:
        stop += timedelta(days=1)
    return int((stop - now).total_seconds())


def free_ram_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1e6
    return 0.0


def gpu_busy() -> bool:
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=process_name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20, check=False).stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return True
    return any(k in out for k in ("python", "ollama", "llama", "vllm"))


def notify(title: str, msg: str) -> None:
    sys.path.insert(0, str(BACKEND / "scripts"))
    from autorun import alert
    alert(title, msg)


def detached(cmd: list[str], name: str, log: Path) -> list[str] | None:
    """The command that runs a job as its own transient systemd service (None if systemd-run is missing).
    A tick called from a timer's service lives in that service's control group, and systemd kills the whole group
    when the service finishes: this killed both jobs 3 s after the 18:29 check on 30 Sep 2026. A transient service
    is started by the user manager itself, so it outlives the caller. Not a timer or a unit file: it ends with the job
    (a scope was tried first and did not survive the caller in a real test)."""
    if not shutil.which("systemd-run"):
        return None
    return ["systemd-run", "--user", "--collect", "--quiet", f"--unit=airp-research-{name}",
            f"--description=airp research: {name}", f"--working-directory={BACKEND}",
            f"--setenv=PATH={os.environ.get('PATH', '/usr/bin:/bin')}",
            "-p", f"StandardOutput=append:{log}", "-p", "StandardError=inherit", "--", *cmd]


def start(job: dict[str, Any], now: datetime) -> None:
    logs = QDIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cmd = list(job["cmd"])
    if job["kind"] == "gpu":
        cmd = ["timeout", str(seconds_to_stop(now)), *cmd]
    cmd = ["systemd-inhibit", "--what=idle:sleep", f"--why=airp research: {job['name']}", *cmd]
    log = logs / f"{job['name']}.log"
    with log.open("a") as f:
        f.write(f"\n=== start {now.isoformat(timespec='seconds')}\n")
        f.flush()
        unit = detached(cmd, job["name"], log)
        if unit and subprocess.run(unit, cwd=BACKEND, stdout=f, stderr=subprocess.STDOUT, check=False).returncode == 0:
            return
        subprocess.Popen(cmd, cwd=BACKEND, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)


def tick(now: datetime | None = None, dry: bool = False) -> list[str]:
    """One pass over the queue; returns what it did (for logs and tests)."""
    now = (now or datetime.now(LOCAL)).astimezone(LOCAL)
    jobs = load()
    done = {j["name"] for j in jobs if is_done(j)}
    did: list[str] = []
    for job in jobs:
        name = job["name"]
        if name in done or job.get("paused"):
            continue
        if any(r not in done for r in job.get("requires", [])):
            continue
        if job["kind"] == "gate":
            if not job.get("notified"):
                job["notified"] = now.isoformat(timespec="seconds")
                did.append(f"gate {name}: waiting for approval")
                if not dry:
                    notify("research", f"{name}: {job.get('note', 'needs a human check before the next step')}")
            continue
        if running(job["match"]):
            continue
        if job["kind"] == "gpu":
            if not gpu_window(now):
                continue
            if gpu_busy() or free_ram_gb() < MIN_FREE_RAM_GB:
                did.append(f"{name}: GPU busy or RAM low, next tick")
                continue
        did.append(f"start {name}")
        if not dry:
            start(job, now)
            if job["kind"] == "gpu":
                break  # one GPU job at a time
            time.sleep(1)
    if not dry:
        save(jobs)
    return did


def status() -> list[dict[str, Any]]:
    out = []
    for job in load():
        d, t = (0, 0) if job["kind"] == "gate" else progress(job["done"])
        out.append({"name": job["name"], "kind": job["kind"], "paused": bool(job.get("paused")),
                    "running": bool(job.get("match") and running(job["match"])), "done": is_done(job),
                    "progress": [d, t], "requires": job.get("requires", []), "note": job.get("note", "")})
    return out


def set_flag(name: str, key: str, value: bool) -> None:
    jobs = load()
    for j in jobs:
        if j["name"] == name:
            j[key] = value
    save(jobs)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "tick":
        print("\n".join(tick()) or "nothing to do")
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd in ("pause", "resume"):
        set_flag(sys.argv[2], "paused", cmd == "pause")
    elif cmd == "init":
        if QUEUE.exists():
            raise SystemExit(f"{QUEUE} exists; not overwritten")
        save(DEFAULT_JOBS)
    elif cmd == "approve":
        set_flag(sys.argv[2], "approved", True)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
