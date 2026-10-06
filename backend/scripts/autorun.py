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
import re
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
CONSENSUS = "scripts/consensus_shadow.py"
DAY_CALLS = "scripts/day_calls_shadow.py"  # D13: the live day calls scored after their day (no orders)
DAY_FEASIBILITY = "scripts/day_feasibility.py"  # which tech stocks can be day-traded cheaply (user, 4 Oct 2026)
M1_SHADOW = "scripts/m1_shadow.py"  # month-end Treasuries, a no-money shadow (the user's yes, 1 Oct 2026)
EVENT_READERS = {"scripts/ai_picks.py", "scripts/guidance_shadow.py", "scripts/net_read_shadow.py",
                 "scripts/self_improve.py", "scripts/learn_loop.py", CONSENSUS}
# records added after the outside review of 1 Oct 2026 (docs/PLAN_60_V2.md, "Outside review, second part"): they
# read what the run wrote and decide nothing, so they are side steps and run in live mode only
EVIDENCE = "scripts/evidence_bundle.py"    # one evidence file per decision, in the run that made it
ACCOUNT = "scripts/account_view.py"        # the paper account across both books (read-only at the broker)
FUNNEL = "scripts/funnel.py"               # weekly: where every release stopped, and why
CONTRIBUTION = "scripts/ai_contribution.py"  # weekly: the AI's picks against the same sleeve dealt at random
THROUGHPUT = "scripts/throughput.py"       # weekly: stage times, decision latency, time left before the open
# recorded in the log, but their exit code never becomes the job's result
SIDE_STEPS = {CONSENSUS, DAY_FEASIBILITY, DAY_CALLS, M1_SHADOW, EVIDENCE, ACCOUNT, FUNNEL, CONTRIBUTION, THROUGHPUT}
# a step that failed for a network reason is retried once: each of these is idempotent (ledgers skip what they have
# seen; broker orders carry client ids, so a resend is refused, not duplicated)
RETRYABLE = {"scripts/forward_events.py", "scripts/forward_allocator.py", "scripts/broker_sync.py", "scripts/ai_picks.py"}
TRANSIENT = re.compile(r"Temporary failure in name resolution|Name or service not known|Connection (reset|refused|"
                       r"aborted)|timed out|HTTP (429|502|503|504)|RemoteDisconnected|ConnectTimeout|ReadTimeout|"
                       r"URLError|YFRateLimitError|Too Many Requests|No objects to concatenate|"
                       r"no SPY closing prices", re.IGNORECASE)  # the last three: Yahoo returned nothing
RETRY_WAIT = 90
RUNNING = "running.json"         # the job at work; a restart that kills the run leaves it behind (see `resume`)
RESUME_JOBS = {"events"}         # jobs whose steps are safe to run again after a cut-off run (see RETRYABLE)
RESUME_MAX_AGE = timedelta(hours=6)
RESUME_GAP = timedelta(minutes=30)   # no resume this close to the next scheduled event run: that run covers it
EVENT_SLOTS = ((8, 45), (18, 30))    # airp-events.timer, New York time, Mon-Fri
EXPECTED = {"events": 2, "allocator_weekday": 0}  # event runs per weekday; the allocator runs Mondays


def mode() -> str:
    p = FWD / "AUTORUN_MODE"
    return "live" if p.exists() and p.read_text().strip() == "live" else "dry"


def append(name: str, rec: dict[str, Any]) -> None:
    """One JSON line; starts a fresh line if a crash left the last one cut off, so the new record is not glued to it."""
    FWD.mkdir(parents=True, exist_ok=True)
    p = FWD / name
    torn = p.exists() and p.stat().st_size > 0 and not p.read_bytes().endswith(b"\n")
    with p.open("a") as f:
        f.write(("\n" if torn else "") + json.dumps(rec) + "\n")


def heartbeats() -> list[dict[str, Any]]:
    """Every readable heartbeat line. A line cut off by a crash or power loss is skipped: one bad line must not stop
    every later run from recording itself."""
    hb = FWD / "heartbeat.jsonl"
    out = []
    for line in hb.read_text().splitlines() if hb.exists() else []:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def completed(r: dict[str, Any]) -> bool:
    """A run that did its job: a clean exit, or an event run whose runner finished (it printed its scoreboard, so
    `missed_total` is set) while a later step raised an alert, e.g. a paper-broker order that did not fill."""
    return r.get("rc") == 0 or (r.get("job") == "events" and r.get("missed_total") is not None)


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
    try:  # no desktop session (or no notify-send) must never stop the run that is alerting
        subprocess.run(["notify-send", "-u", "critical", f"Paper book: {job}", msg], check=False,
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
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
        # the consensus shadow (PLAN_60_V2), live only, CPU: right after the agents' labels, so its lines are
        # written (and pushed with this run) before the open even when a later step takes long
        consensus = [] if dry else [[PY, CONSENSUS]]
        m1 = [] if dry else [[PY, M1_SHADOW]]  # live only, CPU; downloads only when a month is due; a side step
        # before the long steps, so the evidence files and the account view are pushed with the decisions
        records = [] if dry else [[PY, EVIDENCE], [PY, ACCOUNT]]
        feasibility = [] if dry else [[PY, DAY_FEASIBILITY], [PY, DAY_CALLS]]  # market data only; side steps
        return [broker, [PY, "scripts/forward_events.py", *(DRY["events"] if dry else [])], picks, guide, net_read,
                *consensus, *records, longterm, themes, *m1, *feasibility, improve, [*learn, "collect", *largs]]
    if job == "allocator":
        return [[PY, "scripts/forward_allocator.py", *(DRY["allocator"] if dry else [])], broker]
    if job == "review":
        # the monthly loop rides on the Saturday review (it runs once per calendar month); never in dry mode, because
        # its proposals register trials
        monthly = [] if dry else [[*learn, "monthly"]]
        records = [] if dry else [[PY, FUNNEL, "--sweep"], [PY, CONTRIBUTION], [PY, THROUGHPUT],
                                  [PY, EVIDENCE, "--check"]]
        return [[PY, "scripts/weekly_review.py"], [PY, "scripts/failure_review.py"], *monthly,
                [*learn, "review", *largs], *records]
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
    return next((r for r in reversed(heartbeats()) if r.get("job") == job and r.get("mode") == m), {})


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
    try:
        subprocess.run([*git, "add", "backend/results/forward"], check=False, capture_output=True, timeout=120)
        subprocess.run([*git, "commit", "-q", "-m", f"autorun {job} {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", "--",
                        "backend/results/forward"], check=False, capture_output=True, text=True, timeout=120)
        p = subprocess.run([*git, "push", "-q"], check=False, capture_output=True, text=True, timeout=120,
                           env={"GIT_SSH_COMMAND": f"ssh -i {Path.home()}/.ssh/id_ed25519_github",
                                "HOME": str(Path.home()), "PATH": "/usr/bin:/bin"})
    except subprocess.TimeoutExpired as e:
        alert(job, f"git did not answer in 120 s ({' '.join(e.cmd[3:5]) if isinstance(e.cmd, list) else 'git'})")
        return
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


def safe_step(cmd: list[str], inhibit: bool) -> subprocess.CompletedProcess[str]:
    """`step`, with a step that hangs past its time limit or cannot start turned into a failed step: the run still
    writes its log, heartbeat and alert instead of dying without a trace."""
    from app.forward.ledger import Ledger
    from app.forward.step_result import outcome
    started = time.monotonic()
    try:
        result = step(cmd, inhibit)
    except (OSError, subprocess.TimeoutExpired) as e:
        result = subprocess.CompletedProcess(cmd, 124, "", f"{cmd[1] if cmd[1:2] else cmd[0]}: {type(e).__name__}: {e}"[:300] + "\n")
    record = outcome(cmd, result.returncode, result.stdout, result.stderr, time.monotonic() - started)
    Ledger(FWD / "step_records.jsonl").append("step", **record)
    if record["status"] == "failed" and result.returncode == 0:
        result.returncode = 1
    return result


def run(job: str, resumed_from: str | None = None) -> int:
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
        (FWD / RUNNING).write_text(json.dumps({"job": job, "mode": m, "start": start.isoformat(timespec="seconds")}) + "\n")
        rc, out = 0, "" if wait_online() else "(network still down after 2 minutes; ran anyway)\n"
        if resumed_from:
            out = f"(resumed at boot: the run started {resumed_from} was cut off)\n" + out
        inhibit = can_inhibit()
        skip_dependents = False  # a failed event runner: its readers must not run on a half-written ledger
        for cmd in commands(job, m):
            if skip_dependents and cmd[1:2] and cmd[1] in EVENT_READERS:
                out += f"(skipped {cmd[1]}: the event runner failed)\n"
                continue
            r = safe_step(cmd, inhibit)
            if cmd[1:2] and cmd[1] in SIDE_STEPS:
                out += r.stdout + r.stderr + (f"({cmd[1]} failed; the run's result is not affected)\n"
                                              if r.returncode != 0 else "")
                continue
            if r.returncode != 0 and cmd[1:2] and cmd[1] in RETRYABLE and TRANSIENT.search(r.stdout + r.stderr):
                out += r.stdout + r.stderr + f"(transient failure in {cmd[1]}; retrying once in 90 s)\n"
                time.sleep(RETRY_WAIT)
                r = safe_step(cmd, inhibit)
            out += r.stdout + r.stderr
            rc = rc or r.returncode
            if r.returncode != 0 and cmd[1:2] == ["scripts/forward_events.py"]:
                skip_dependents = True
            log.write_text(out)  # after every step: a run cut off by a restart still leaves its log so far
        log.write_text(out)
    # the allocator refuses a second run on the same day with a clear message: not a failure
    if job == "allocator" and rc != 0 and "already ran today" in out:
        rc = 0
    end = datetime.now(UTC)
    prev = last_heartbeat(job, m).get("missed_total")
    beat = {"job": job, "mode": m, "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"), "rc": rc, "log": str(log.relative_to(BACKEND)),
            "missed_total": missed_total(out) if job == "events" else None}
    if resumed_from:
        beat["resumed_from"] = resumed_from
    append("heartbeat.jsonl", beat)
    (FWD / RUNNING).unlink(missing_ok=True)
    if rc != 0:
        alert(job, f"failed (exit {rc}); log {log.name}: {out.strip().splitlines()[-1][:200] if out.strip() else ''}")
    for f in scan(job, out, prev):
        alert(job, f)
    # push after every completed run: an event run whose runner finished has a whole ledger even when a later step
    # alerted, and the push is what gives its decisions an outside timestamp before their results are known
    if m == "live" and completed(beat) and job in ("events", "allocator", "review", "learn"):
        push(job)
    post_run(job)
    return rc


def next_event_slot(now: datetime) -> datetime:
    """The next scheduled event run after `now` (airp-events.timer: weekdays 08:45 and 18:30 New York time)."""
    et = now.astimezone(NY)
    for d in range(8):
        day = (et + timedelta(days=d)).date()
        if day.weekday() >= 5:
            continue
        for h, mi in EVENT_SLOTS:
            slot = datetime(day.year, day.month, day.day, h, mi, tzinfo=NY)
            if slot > et:
                return slot
    raise AssertionError("no weekday in eight days")


def resume(now: datetime | None = None, settle_s: float = 120.0) -> int:
    """At boot (airp-resume.service): finish a scheduled run that a restart cut off. A run writes `running.json`
    when it starts work and removes it with its heartbeat; a file left behind means the run never finished (1 Oct
    2026: a restart an hour into the afternoon run; nothing was pushed and no digest was sent until the next
    morning). Only event runs are run again (their steps skip what is already done), only within 6 hours, and not
    when the next scheduled run is under 30 minutes away or another run is already at work. Anything else is
    reported and left to the next scheduled run."""
    p = FWD / RUNNING
    if not p.exists():
        print("nothing to resume")
        return 0
    time.sleep(settle_s)  # right after boot: let the network, the desktop session and the GPU come up
    with (FWD / "autorun.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("a run is at work: nothing to resume")
            return 0
        try:
            was = json.loads(p.read_text())
            job, start = str(was["job"]), datetime.fromisoformat(was["start"])
        except (ValueError, KeyError, TypeError):
            p.unlink(missing_ok=True)
            alert("resume", "a run was cut off, but its marker could not be read; the next scheduled run covers it")
            return 0
        now = now or datetime.now(UTC)
        why = ("only event runs are resumed" if job not in RESUME_JOBS else
               "the mode changed since" if was.get("mode") != mode() else
               f"more than {RESUME_MAX_AGE.seconds // 3600} hours ago" if now - start > RESUME_MAX_AGE else
               "the next scheduled run is under 30 minutes away" if next_event_slot(now) - now < RESUME_GAP else "")
        p.unlink(missing_ok=True)
    if why:
        alert(job, f"the run started {start:%Y-%m-%d %H:%M} UTC was cut off (restart?); not resumed: {why}. "
                   "The next scheduled run covers it")
        return 0
    alert(job, f"the run started {start:%Y-%m-%d %H:%M} UTC was cut off (restart?); resuming it now")
    return run(job, resumed_from=was["start"])


def post_run(job: str) -> None:
    """After a job: let the research queue start/restart what is due, after the afternoon event run send the daily
    digest, and after the daily check refresh the desktop app's charts. None may affect the job's result."""
    extra = [[PY, "scripts/research_queue.py", "tick"]]
    if job == "events" and datetime.now(ZoneInfo("America/Los_Angeles")).hour >= 12:
        extra.append([PY, "scripts/digest.py", "--send"])
    if job == "check":  # refresh the desktop app's chart data once a day
        extra.append([PY, "scripts/desktop_export.py"])
    for cmd in extra:
        try:
            subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def disk_low(path: Path = BACKEND, min_free_gb: float = 20.0) -> str | None:
    """An alert when the disk holding the ledgers runs low: a full disk would break every append silently."""
    import shutil
    free = shutil.disk_usage(path).free / 1e9
    return f"disk space low: {free:.1f} GB free (alert below {min_free_gb:.0f} GB)" if free < min_free_gb else None


def unanchored(repo: Path = REPO, ref: str = "@{upstream}") -> list[str]:
    """Ledgers that no longer extend their pushed copy. A hash chain shows a change inside it, but not a missing
    end: with its last records removed it still verifies. The copy on GitHub is the outside anchor: every ledger
    under results/forward must begin with exactly the bytes that were last pushed."""
    git = ["git", "-C", str(repo)]
    try:
        names = subprocess.run([*git, "ls-tree", "-r", "--name-only", ref, "backend/results/forward"],
                               capture_output=True, text=True, timeout=60, check=True).stdout.splitlines()
        bad = []
        for name in names:
            if not name.endswith("ledger.jsonl"):
                continue
            pushed = subprocess.run([*git, "cat-file", "blob", f"{ref}:{name}"], capture_output=True, timeout=60,
                                    check=True).stdout
            local = repo / name
            if not local.exists():
                bad.append(f"{name}: pushed, but missing here")
            elif not local.read_bytes().startswith(pushed):
                bad.append(f"{name}: does not extend its pushed copy (records removed or changed)")
        return bad
    except (OSError, subprocess.SubprocessError) as e:
        return [f"ledger anchor check could not run ({type(e).__name__})"]


def check(today: date | None = None) -> list[str]:
    """Weekdays in the last 7 days whose expected runs are missing from the heartbeat (in this mode)."""
    m = mode()
    recs = [r for r in heartbeats() if r.get("mode") == m and completed(r)]
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
    reasons = go_live_reasons(heartbeats(), FWD / "events_autodry" / "ledger.jsonl")
    if reasons:
        return "still in DRY mode: " + "; ".join(reasons) + ". Fix, or switch by hand: scripts/autonomy.sh live"
    (FWD / "AUTORUN_MODE").write_text("live\n")
    append("go_live.jsonl", {"at": datetime.now(UTC).isoformat(timespec="seconds"), "switched": "live",
                             "rule": "go_live_reasons() empty"})
    return "switched to LIVE: the dry run was clean. The forward test runs for real from the next scheduled job."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", choices=("events", "allocator", "review", "check", "learn", "resume"))
    args = ap.parse_args()
    if args.job == "resume":
        sys.exit(resume())
    if args.job == "check":
        gaps = check()
        for g in gaps:
            alert("check", "missed run: " + g)
        low = disk_low()
        if low:
            alert("check", low)
        if mode() == "live":
            for x in unanchored():
                alert("check", "ledger anchor: " + x)
        post_run("check")
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
