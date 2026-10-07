"""Allowlisted app runs. Shares autorun's lock; writes its own history, never scheduled heartbeats.

The desktop launches this worker detached, so closing the window does not interrupt a paper order.
No arbitrary commands, strategy trials, commits, configuration changes or real-money endpoints.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.forward.ledger import Ledger
from app.forward.step_result import outcome

BACKEND = Path(__file__).resolve().parents[1]
PAPER_ACTIONS = {"paper_ai", "paper_sync", "paper_research_test", "paper_auto", "paper_auto_tech100"}
ACTIONS = PAPER_ACTIONS | {"recovery_tests", "backend_tests", "desktop_tests", "improvement_review",
                           "release_check", "backup", "restore_probe", "rollback_probe", "institutional_report", "live_research_test", "budget_experiment", "horizon_review", "full_auto", "autopilot_day"}
NY = ZoneInfo("America/New_York")


def identity(pid: int) -> str:
    """Boot and process birth time distinguish a surviving worker from a reused PID."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return ""
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + stat[19]
    except (OSError, IndexError):
        return ""


def next_slot(now: datetime) -> datetime:
    """Existing timer slots, in New York time (including daylight saving)."""
    local = now.astimezone(NY)
    slots: list[datetime] = []
    for offset in range(8):
        day = local + timedelta(days=offset)
        hours = [(21, 0)]  # daily check
        if day.weekday() < 5:
            hours += [(8, 45), (18, 30)]
        if day.weekday() == 0:
            hours += [(18, 0)]
        if day.weekday() == 5:
            hours += [(10, 0)]
        for hour, minute in hours:
            slot = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if slot > local:
                slots.append(slot)
    return min(slots).astimezone(UTC)


def commands(action: str, node: str) -> list[tuple[str, list[str]]]:
    py = sys.executable
    if action in {"paper_auto", "paper_auto_tech100", "full_auto", "autopilot_day"}:
        return []  # Persistent controller executes allowlisted actions one cycle at a time.
    if action == "budget_experiment":
        return [("Compare neutral and budget-aware judgments on identical evidence",
                 [py, "-u", "scripts/live_research_test.py", "--budget-experiment"])]
    if action == "horizon_review":
        return [("Refresh prospective horizon price outcomes", [py, "-u", "scripts/horizon_review.py"])]
    if action in ("live_research_test", "paper_research_test"):
        research = [("Fresh web research, company latency and horizon proposals",
                     [py, "-u", "scripts/live_research_test.py"])]
        return research + (commands("paper_ai", node) if action == "paper_research_test" else [])
    if action == "paper_ai":
        return [("Jan researches filings, then Bonsai judges", [py, "-u", "scripts/forward_events.py", "--jan-research"]),
                ("Save decision evidence", [py, "-u", "scripts/evidence_bundle.py"]),
                ("Sync AI paper trades", [py, "-u", "scripts/ai_picks.py"]),
                ("Snapshot paper account", [py, "-u", "scripts/account_view.py"]),
                ("Collect learning evidence", [py, "-u", "scripts/learn_loop.py", "collect", "--no-gpu"]),
                ("Automatically review existing learning candidates", [py, "-u", "scripts/improvement_report.py", "--review"])]
    if action == "paper_sync":
        return [("Sync core paper orders", [py, "-u", "scripts/broker_sync.py"]),
                ("Sync AI paper trades", [py, "-u", "scripts/ai_picks.py"]),
                ("Snapshot paper account", [py, "-u", "scripts/account_view.py"])]
    if action == "recovery_tests":
        return [("Paper order and outage recovery tests", [py, "-m", "pytest", "-q", "tests/test_recovery.py",
                 "tests/test_account_risk.py", "tests/test_forward_events_main.py", "tests/test_institutional_tools.py"])]
    if action == "backend_tests":
        return [("Backend tests", [py, "-m", "pytest", "-q"])]
    if action == "improvement_review":
        return [("Review existing learning shadows", [py, "-u", "scripts/improvement_report.py", "--review"])]
    if action == "institutional_report":
        return [("Reconcile AI contribution", [py, "-u", "scripts/ai_contribution.py"]),
                ("Update engineering records", [py, "-u", "scripts/institutional_report.py", "--write"])]
    if action in ("backup", "restore_probe", "rollback_probe"):
        verb = {"backup": "backup", "restore_probe": "restore", "rollback_probe": "rollback_probe"}[action]
        return [("Preserve or rehearse a snapshot", [py, "-u", "scripts/ops_tool.py", verb])]
    if action == "release_check":
        return [("Record candidate source hashes", [py, "scripts/ops_tool.py", "start_checks"]),
                ("Verify backend", [py, "-m", "pytest", "-q"]),
                ("Check Python safety modules", [py, "-m", "ruff", "check", "app/portfolio", "app/forward"]),
                ("Check safety types", [py, "-m", "mypy", "--strict", "--follow-imports=silent", "app/portfolio", "app/forward"]),
                *[("Check app syntax " + str(p.name), [node, "--check", str(p)])
                  for folder in ("src", "public", "electron") for p in sorted((BACKEND.parent / "desktop" / folder).glob("*.js"))],
                *commands("desktop_tests", node),
                ("Preserve checked release candidate", [py, "scripts/ops_tool.py", "release"])]
    if action == "desktop_tests":
        files = sorted(str(p) for p in (BACKEND.parent / "desktop" / "tests").glob("*.test.js"))
        if not files:
            raise ValueError("Desktop test files are missing from the checkout")
        return [("Desktop tests", [node, "--test", *files])]
    raise ValueError("Unknown app action")


def preflight(action: str, fwd: Path, now: datetime) -> str | None:
    if action in PAPER_ACTIONS:
        mode = fwd / "AUTORUN_MODE"
        if not mode.exists() or mode.read_text().strip() != "live":
            return "Paper runs require the existing live paper mode. The app does not change the mode."
        if action in ("paper_ai", "paper_research_test") and (fwd / "HALT").exists():
            return "Trading is halted or reducing. Resume with the existing kill-switch control first."
    required = 360 if action == "paper_sync" else 1800 if action in PAPER_ACTIONS or action in ("live_research_test", "budget_experiment") else 360
    if (next_slot(now) - now).total_seconds() < required:
        return "An automatic job is due soon. Try again after that job finishes."
    return None


def execute(cmd: list[str], timeout: float, log: Path, lock_fd: int) -> tuple[int, bool]:
    """Stream to disk. Timeout kills only this worker's exact child process group."""
    started = time.monotonic()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "ELECTRON_RUN_AS_NODE": "1",
           "AIRP_AUTORUN_LOCK_FD": str(lock_fd)}
    with log.open("ab") as output:
        offset = output.tell()
        with subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=output, stderr=output,
                              start_new_session=True, pass_fds=(lock_fd,)) as child:
            try:
                rc = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                # The script may exit before a model server it started. Clean up its group too.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output.write(b"\nStopped at the time limit; the next automatic job has priority.\n")
                rc = 124
    # broker_sync reports errors without a nonzero exit code: do not label them a success.
    warning = False
    with log.open("r", errors="replace") as output:
        output.seek(offset)
        content = output.read()
        for line in content.splitlines():
            if "BROKER ALERT:" in line:
                rc = rc or 1
            if (line.startswith(("LEARN ALERT", "BROKER ALERT", "DRAWDOWN"))
                    or "broker: skipped" in line.lower() or "broker mirror skipped" in line.lower()):
                warning = True
    structured = outcome(cmd, rc, content, "", time.monotonic() - started)
    if structured["status"] == "failed":
        rc = rc or 1
    if structured["status"] in ("warning", "skipped"):
        warning = True
    Ledger(log.parent / "steps.jsonl").append("step", **structured)
    return rc, warning


def run(action: str, job: Path, node: str, backend: Path = BACKEND) -> int:
    if action in {"paper_auto", "paper_auto_tech100"}:
        from paper_autopilot import run as continuous
        return continuous(job, node, backend)
    if action == "full_auto":
        from full_auto import run as autopilot
        return autopilot(job, node, backend)
    if action == "autopilot_day":
        from algo_engine import run_job
        return run_job(job)
    fwd = backend / "results" / "forward"
    status_path = job / "status.json"
    status: dict[str, Any] = json.loads(status_path.read_text())
    status.update(pid=os.getpid(), identity=identity(os.getpid()), state="running",
                  started_at=datetime.now(UTC).isoformat(), steps=[], message="Starting")

    def save(**changes: Any) -> None:
        status.update(changes)
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status) + "\n")
        tmp.replace(status_path)

    def finish(state: str, message: str, rc: int) -> int:
        save(state=state, message=message, code=rc, finished_at=datetime.now(UTC).isoformat())
        return rc

    save()
    try:
        fwd.mkdir(parents=True, exist_ok=True)
        with (fwd / "autorun.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return finish("blocked", "Another app or automatic job is running. Try again after it finishes.", 2)
            reason = preflight(action, fwd, datetime.now(UTC))
            if reason:
                return finish("blocked", reason, 2)
            warning = False
            os.environ["AIRP_DESKTOP_JOB"] = str(job.resolve())
            for label, cmd in commands(action, node):
                now = datetime.now(UTC)
                available = (next_slot(now) - now).total_seconds() - 300
                if available <= 0:
                    return finish("blocked", "Remaining steps deferred to the next automatic run.", 2)
                if action in ("paper_ai", "paper_research_test") and (fwd / "HALT").exists():
                    return finish("blocked", "Trading was halted during the run; remaining steps stopped.", 2)
                if action in PAPER_ACTIONS and (fwd / "AUTORUN_MODE").read_text().strip() != "live":
                    return finish("blocked", "Paper mode changed during the run; remaining steps stopped.", 2)
                step: dict[str, Any] = {"label": label, "state": "running", "started_at": now.isoformat()}
                status["steps"].append(step)
                save(message=label)
                print(f"\n=== {label} ===", flush=True)
                rc, warned = execute(cmd, min(available, 6 * 3600 if action in PAPER_ACTIONS or action in ("live_research_test", "budget_experiment") else 600),
                                     job / "output.log", lock.fileno())
                warning = warning or warned
                step.update(state=("warning" if warned else "succeeded") if rc == 0 else "failed", code=rc,
                            finished_at=datetime.now(UTC).isoformat())
                save()
                if rc:
                    if action == "paper_sync":
                        # Preserve a read-only account snapshot even when submissions fail.
                        snapshot = [sys.executable, "-u", "scripts/account_view.py"]
                        snap_rc, snap_warn = execute(snapshot, min(available, 120), job / "output.log", lock.fileno())
                        status["steps"].append({"label": "Snapshot after sync failure",
                            "state": "failed" if snap_rc else "warning" if snap_warn else "succeeded", "code": snap_rc})
                    return finish("failed", f"{label} failed (exit {rc}). Review the log before retrying.", rc)
            return finish("warning" if warning else "succeeded",
                          "Finished with warnings; review the log." if warning else "All steps finished successfully.", 0)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        return finish("failed", f"{type(exc).__name__}: {str(exc)[:300]}", 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=sorted(ACTIONS))
    ap.add_argument("job", help="existing app job directory")
    ap.add_argument("--node", required=True)
    args = ap.parse_args()
    job = Path(args.job).resolve()
    if job.parent != (BACKEND / "results" / "desktop_runs").resolve() or not job.is_dir():
        raise SystemExit("Invalid app job directory")
    raise SystemExit(run(args.action, job, args.node))


if __name__ == "__main__":
    main()
