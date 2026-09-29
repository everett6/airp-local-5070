"""Autonomy runner for the forward test (docs/PLAN_FORWARD.md "Decisions": live on 5 Oct after a Wed–Fri dry run).
Started by the systemd user timers in deploy/systemd/ (scripts/autonomy.sh installs them), or by hand.

    python scripts/autorun.py events       # the event runner (forward_events.py)
    python scripts/autorun.py allocator    # the weekly allocator (forward_allocator.py)
    python scripts/autorun.py review       # weekly_review.py + failure_review.py
    python scripts/autorun.py check        # heartbeat check: alerts on expected runs that did not happen; on and
                                           # after 2 Oct also the automatic dry -> live switch (go_live_reasons)
    python scripts/autorun.py learn        # the monthly learning loop by hand (the Saturday review also runs it)

Mode: results/forward/AUTORUN_MODE holds "dry" or "live"; missing means dry. Dry runs write to their own folders
(results/forward/events_autodry, allocator_autodry) and never touch the real ledgers or git. Live runs use the real
ledgers and commit and push them (the push gives the hash chain an outside timestamp).
Events and allocator jobs also run scripts/broker_sync.py (the Alpaca paper mirror; a no-op without keys).
Every run: one job at a time (a lock), a heartbeat line (results/forward/heartbeat.jsonl) with its exit code and log,
and an alert (results/forward/alerts.jsonl + a desktop notification) on a failure, a drawdown flag, or missed
decisions. The kill switch, mandate, order gate and drawdown limit stay in force: this only starts the same scripts.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
REPO = BACKEND.parent
FWD = BACKEND / "results" / "forward"
PY = str(BACKEND / ".venv" / "bin" / "python")
NY = ZoneInfo("America/New_York")
# dry runs cover the prep week (real releases from Mon 28 Sep); live runs keep the forward test's own start
DRY = {"events": ["--dir", "results/forward/events_autodry", "--start", "2026-09-28"],
       "allocator": ["--dir", "results/forward/allocator_autodry"],
       "broker": ["--dry", "--dir", "results/forward/allocator_autodry", "--out", "results/forward/broker_autodry"],
       "learn": ["--dir", "results/forward/events_autodry", "--out", "results/forward/signals_autodry"],
       "net_read": ["--dir", "results/forward/events_autodry"],
       "ai_picks": ["--dry", "--events", "results/forward/events_autodry", "--dir", "results/forward/ai_picks_autodry"],
       "longterm": ["--events", "results/forward/events_autodry"],
       "self_improve": ["--dir", "results/forward/events_autodry"]}
GO_LIVE_ON = date(2026, 10, 2)   # automatic switch date; the user explicitly selected live paper mode on 29 Sep
LIVE_FROM = date(2026, 9, 30)    # first eligible live filing date after the user-directed early switch
DRY_FROM = date(2026, 9, 28)
MIN_GOOD_EVENT_RUNS = 8          # of the 10 scheduled Mon-Fri
# steps that read the event runner's ledger; the broker, long-term and theme steps are independent and always run
EVENT_READERS = {"scripts/ai_picks.py", "scripts/guidance_shadow.py", "scripts/net_read_shadow.py",
                 "scripts/self_improve.py", "scripts/learn_loop.py"}
EXPECTED = {"events": 2, "allocator_weekday": 0}  # event runs per weekday; the allocator runs Mondays


def mode() -> str:
    p = FWD / "AUTORUN_MODE"
    return "live" if p.exists() and p.read_text().strip() == "live" else "dry"


def append(name: str, rec: dict[str, Any]) -> None:
    FWD.mkdir(parents=True, exist_ok=True)
    with (FWD / name).open("a") as f:
        f.write(json.dumps(rec) + "\n")


def ntfy_topic() -> str:
    for line in (BACKEND / ".env").read_text().splitlines() if (BACKEND / ".env").exists() else []:
        if line.startswith("NTFY_TOPIC="):
            return line.split("=", 1)[1].strip()
    return ""


def wait_online(host: str = "www.sec.gov", tries: int = 24, pause: float = 5.0) -> bool:
    """True once `host` accepts a connection. A catch-up run fired at boot can start before the network is up
    (29 Sep 2026: the network came up 7 s after the run started), so jobs wait up to about 2 minutes for it."""
    for i in range(tries):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            if i < tries - 1:
                time.sleep(pause)
    return False


def alert(job: str, msg: str) -> None:
    """alerts.jsonl + a desktop notification + a phone push via ntfy.sh (user-approved 2026-09-27) when a topic is set
    in backend/.env. The push carries only the job name and the message."""
    append("alerts.jsonl", {"at": datetime.now(UTC).isoformat(timespec="seconds"), "job": job, "msg": msg})
    subprocess.run(["notify-send", "-u", "critical", f"Paper book: {job}", msg], check=False,
                   capture_output=True, timeout=10)
    topic = ntfy_topic()
    if topic:
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.encode()[:3000], method="POST",
                                         headers={"Title": f"Paper book: {job}", "Priority": "high"})
            urllib.request.urlopen(req, timeout=15).read()
        except OSError as e:
            append("alerts.jsonl", {"at": datetime.now(UTC).isoformat(timespec="seconds"), "job": "ntfy",
                                    "msg": f"phone push failed ({type(e).__name__})"})


def commands(job: str, m: str) -> list[list[str]]:
    dry = m == "dry"
    broker = [PY, "scripts/broker_sync.py", *(DRY["broker"] if dry else [])]  # paper broker mirror (skips without keys)
    learn = [PY, "scripts/learn_loop.py"]
    largs = DRY["learn"] if dry else []
    if job == "events":  # broker first: the 08:45 ET run is inside Alpaca's market-on-open window
        net_read = [PY, "scripts/net_read_shadow.py", *(DRY["net_read"] if dry else [])]  # arm C2, a shadow
        picks = [PY, "scripts/ai_picks.py", *(DRY["ai_picks"] if dry else [])]  # the untested AI-picks sleeve
        guide = [PY, "scripts/guidance_shadow.py",
                 "--events", "results/forward/events_autodry" if dry else "results/forward/events",
                 "--out", "results/forward/guidance_shadow_autodry" if dry else "results/forward/guidance_shadow"]
        # long-term picks: a no-money shadow, one ledger in both modes (its first cohort, 1 Oct, falls in the dry run)
        longterm = [PY, "scripts/longterm_picks.py", *(DRY["longterm"] if dry else [])]
        themes = [PY, "scripts/themes.py"]  # the theme track and AI-bubble gauge: a shadow, one ledger in both modes
        improve = [PY, "scripts/self_improve.py", *(DRY["self_improve"] if dry else [])]  # arm F, a shadow
        return [broker, [PY, "scripts/forward_events.py", *(DRY["events"] if dry else [])], picks, guide, net_read, longterm,
                themes, improve, [*learn, "collect", *largs]]
    if job == "allocator":
        return [[PY, "scripts/forward_allocator.py", *(DRY["allocator"] if dry else [])], broker]
    if job == "review":
        # the monthly loop rides on the Saturday review (it runs once per calendar month); never in dry mode, because
        # its proposals register trials
        monthly = [] if dry else [[*learn, "monthly"]]
        return [[PY, "scripts/weekly_review.py"], [PY, "scripts/failure_review.py"], *monthly,
                [*learn, "review", *largs]]
    if job == "learn":  # proposals register trials: never in dry mode
        return [] if dry else [[*learn, "monthly"]]
    raise SystemExit(f"unknown job {job}")


def missed_total(out: str) -> int | None:
    """The event runner's scoreboard total of missed decisions (the last '"missed": N' in its output)."""
    n = None
    for line in out.splitlines():
        if line.strip().startswith('"missed":'):
            try:
                n = int(line.split(":", 1)[1].strip().rstrip(","))
            except ValueError:
                pass
    return n


def last_heartbeat(job: str, m: str) -> dict[str, Any]:
    hb = FWD / "heartbeat.jsonl"
    recs = [json.loads(x) for x in hb.read_text().splitlines()] if hb.exists() else []
    return next((r for r in reversed(recs) if r.get("job") == job and r.get("mode") == m), {})


def scan(job: str, out: str, prev_missed: int | None) -> list[str]:
    """Things in a job's output that deserve an alert."""
    flags = []
    if job == "allocator" and "DRAWDOWN" in out:
        flags.append("drawdown flag: " + next(x for x in out.splitlines() if "DRAWDOWN" in x)[:200])
    for tag in ("BROKER ALERT:", "LEARN ALERT:"):
        flags += [x.split(tag, 1)[1].strip()[:200] for x in out.splitlines() if tag in x]
    n = missed_total(out) if job == "events" else None
    if n is not None and n > (prev_missed or 0):
        flags.append(f"{n - (prev_missed or 0)} new missed decision(s) ({n} in total)")
    return flags


def push(job: str) -> None:
    """Commit only the forward-test folder (nothing else that may be staged), then push whatever is unpushed; the
    allocator makes its own commit, which this push also carries."""
    git = ["git", "-C", str(REPO)]
    subprocess.run([*git, "add", "backend/results/forward"], check=False, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", f"autorun {job} {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", "--",
                    "backend/results/forward"], check=False, capture_output=True, text=True)
    p = subprocess.run([*git, "push", "-q"], check=False, capture_output=True, text=True, timeout=120,
                       env={"GIT_SSH_COMMAND": f"ssh -i {Path.home()}/.ssh/id_ed25519_github", "HOME": str(Path.home()),
                            "PATH": "/usr/bin:/bin"})
    if p.returncode != 0:
        alert(job, f"git push failed: {p.stderr.strip()[:200]}")


def can_inhibit() -> bool:
    """Whether logind grants a sleep inhibitor now (right after boot, before the desktop session is active, it
    refuses: "Failed to inhibit: Access denied")."""
    try:
        return subprocess.run(["systemd-inhibit", "--what=idle:sleep", "--why=paper book run", "true"],
                              capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


INHIBIT_DENIED = ("Failed to inhibit: Access denied as the requested operation requires interactive "
                  "authentication. However, interactive authentication has not been enabled by the calling program.")


def step(cmd: list[str], inhibit: bool = True) -> subprocess.CompletedProcess[str]:
    """Run once; retry bare only when logind proves the child never started."""
    pre = ["systemd-inhibit", "--what=idle:sleep", "--why=paper book run"] if inhibit else []
    r = subprocess.run([*pre, *cmd], cwd=BACKEND, capture_output=True, text=True, check=False, timeout=6 * 3600)
    refused = inhibit and r.returncode != 0 and not r.stdout and r.stderr.strip() == INHIBIT_DENIED
    if refused:
        r = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, check=False, timeout=6 * 3600)
    if not inhibit or refused:
        r.stdout = "LEARN ALERT: sleep inhibitor refused; ran without it\n" + r.stdout
    return r


def run(job: str) -> int:
    m = mode()
    logs = FWD / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    start = datetime.now(UTC)
    log = logs / f"{job}_{m}_{start:%Y%m%d_%H%M}.log"
    with (FWD / "autorun.lock").open("w") as lock:
        for _ in range(40):  # wait up to 20 minutes for another job to finish
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(30)
        else:
            alert(job, "skipped: another autorun job held the lock for 20 minutes")
            append("heartbeat.jsonl", {"job": job, "mode": m, "start": start.isoformat(timespec="seconds"), "rc": -1,
                                       "skipped": "lock"})
            return 1
        rc, out = 0, "" if wait_online() else "(network still down after 2 minutes; ran anyway)\n"
        inhibit = can_inhibit()
        skip_dependents = False  # a failed event runner: its readers must not run on a half-written ledger
        for cmd in commands(job, m):
            if skip_dependents and cmd[1:2] and cmd[1] in EVENT_READERS:
                out += f"(skipped {cmd[1]}: the event runner failed)\n"
                continue
            r = step(cmd, inhibit)
            out += r.stdout + r.stderr
            rc = rc or r.returncode
            if r.returncode != 0 and cmd[1:2] == ["scripts/forward_events.py"]:
                skip_dependents = True
        log.write_text(out)
    # the allocator refuses a second run on the same day with a clear message: not a failure
    if job == "allocator" and rc != 0 and "already ran today" in out:
        rc = 0
    end = datetime.now(UTC)
    prev = last_heartbeat(job, m).get("missed_total")
    append("heartbeat.jsonl", {"job": job, "mode": m, "start": start.isoformat(timespec="seconds"),
                               "end": end.isoformat(timespec="seconds"), "rc": rc, "log": str(log.relative_to(BACKEND)),
                               "missed_total": missed_total(out) if job == "events" else None})
    if rc != 0:
        alert(job, f"failed (exit {rc}); log {log.name}: {out.strip().splitlines()[-1][:200] if out.strip() else ''}")
    for f in scan(job, out, prev):
        alert(job, f)
    if m == "live" and rc == 0 and job in ("events", "allocator", "review", "learn"):
        push(job)
    return rc


def check(today: date | None = None) -> list[str]:
    """Weekdays in the last 7 days whose expected runs are missing from the heartbeat (in this mode)."""
    m = mode()
    hb = FWD / "heartbeat.jsonl"
    recs = [json.loads(x) for x in hb.read_text().splitlines()] if hb.exists() else []
    recs = [r for r in recs if r.get("mode") == m and r.get("rc") == 0]
    if m == "live":
        first = LIVE_FROM  # detect a missing first live run even when no live heartbeat exists
    elif recs:
        first = min(datetime.fromisoformat(r["start"]).astimezone(NY).date() for r in recs)
    else:
        return []
    today = today or datetime.now(NY).date()
    gaps = []
    for i in range(1, 8):
        d = today - timedelta(days=i)
        if d < first or d.weekday() >= 5:
            continue
        n = sum(1 for r in recs if r["job"] == "events" and datetime.fromisoformat(r["start"]).astimezone(NY).date() == d)
        if n < EXPECTED["events"]:
            gaps.append(f"{d}: {n} of {EXPECTED['events']} event runs")
        if d.weekday() == EXPECTED["allocator_weekday"] and not any(
                r["job"] == "allocator" and datetime.fromisoformat(r["start"]).astimezone(NY).date() >= d for r in recs):
            gaps.append(f"{d}: no allocator run that week")
    return gaps


def go_live_reasons(recs: list[dict[str, Any]], ledger: Path) -> list[str]:
    """Why the dry run is not good enough to switch to live ([] = switch). Judged on the dry heartbeats since DRY_FROM:
    enough good event runs, at most one failed run, the last two event runs good, a good allocator run, and an intact
    dry event ledger (its hash chain)."""
    from app.forward.ledger import Ledger, LedgerError
    d = [r for r in recs if r.get("mode") == "dry" and r.get("job") in ("events", "allocator")
         and datetime.fromisoformat(r["start"]).astimezone(NY).date() >= DRY_FROM]
    ev = [r for r in d if r["job"] == "events"]
    out = []
    good = sum(r.get("rc") == 0 for r in ev)
    if good < MIN_GOOD_EVENT_RUNS:
        out.append(f"only {good} good dry event runs (need {MIN_GOOD_EVENT_RUNS})")
    bad = sum(r.get("rc") != 0 for r in d)
    if bad > 1:
        out.append(f"{bad} failed dry runs")
    if len(ev) >= 2 and any(r.get("rc") != 0 for r in ev[-2:]):
        out.append("one of the last two dry event runs failed")
    if not any(r["job"] == "allocator" and r.get("rc") == 0 for r in d):
        out.append("no good dry allocator run")
    if not ledger.exists():
        out.append("dry event ledger missing")
    else:
        try:
            Ledger(ledger).verify()
        except LedgerError as e:
            out.append(f"dry event ledger broken: {e}")
    allocator = FWD / "allocator_autodry" / "ledger.jsonl"
    if not allocator.exists():
        out.append("dry allocator ledger missing")
    else:
        try:
            runs = [json.loads(line) for line in allocator.read_text().splitlines()]
            if not runs or not all(r.get("books") and r.get("price_sources") for r in runs):
                out.append("dry allocator ledger incomplete")
        except (OSError, json.JSONDecodeError):
            out.append("dry allocator ledger unreadable")
    return out


def go_live(today: date | None = None) -> str | None:
    """From GO_LIVE_ON, while still dry: switch to live if the dry run was clean (user-delegated 27 Sep: "autonomy live
    on 5 Oct after the dry run"), else stay dry and say why. Returns the alert text, or None when nothing to do."""
    today = today or datetime.now(NY).date()
    if mode() != "dry" or today < GO_LIVE_ON:
        return None
    hb = FWD / "heartbeat.jsonl"
    recs = [json.loads(x) for x in hb.read_text().splitlines()] if hb.exists() else []
    reasons = go_live_reasons(recs, FWD / "events_autodry" / "ledger.jsonl")
    if reasons:
        return "still in DRY mode: " + "; ".join(reasons) + ". Fix, or switch by hand: scripts/autonomy.sh live"
    (FWD / "AUTORUN_MODE").write_text("live\n")
    append("go_live.jsonl", {"at": datetime.now(UTC).isoformat(timespec="seconds"), "switched": "live",
                             "rule": "go_live_reasons() empty"})
    return "switched to LIVE: the dry run was clean. The forward test runs for real from the next scheduled job."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", choices=("events", "allocator", "review", "check", "learn"))
    args = ap.parse_args()
    if args.job == "check":
        gaps = check()
        for g in gaps:
            alert("check", "missed run: " + g)
        print("\n".join(gaps) or "no missed runs")
        msg = go_live()
        if msg:
            alert("go-live", msg)
            print(msg)
        append("heartbeat.jsonl", {"job": "check", "mode": mode(), "start": datetime.now(UTC).isoformat(timespec="seconds"),
                                   "rc": 0, "gaps": len(gaps)})
        return
    sys.exit(run(args.job))


if __name__ == "__main__":
    main()
