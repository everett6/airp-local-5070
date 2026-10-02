"""Autonomy runner: mode file, dry-run folders, alert scanning, missed-run check (no subprocesses run here)."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import autorun


def test_mode_defaults_to_dry_and_dry_never_uses_real_ledgers(tmp_path, monkeypatch):
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    assert autorun.mode() == "dry"
    (tmp_path / "AUTORUN_MODE").write_text("LIVE please")  # anything but exactly "live" stays dry
    assert autorun.mode() == "dry"
    (tmp_path / "AUTORUN_MODE").write_text("live\n")
    assert autorun.mode() == "live"
    for job in ("events", "allocator"):
        dry = " ".join(autorun.commands(job, "dry")[0])
        assert "--dir results/forward/" in dry and "autodry" in dry
        assert "--dir" not in " ".join(autorun.commands(job, "live")[0])


def test_scan_alerts_on_new_missed_and_drawdown_only():
    out = '{\n "decisions": 4,\n "on_time": 4,\n "missed": 3,\n "outcomes": 1\n}'
    assert autorun.missed_total(out) == 3
    assert autorun.scan("events", out, 3) == []  # same total as last time: no new alert
    assert autorun.scan("events", out, 1) == ["2 new missed decision(s) (3 in total)"]
    dd = "DRAWDOWN {'book': 'master+brakes', 'from_peak': 0.26, 'action': 'alert'}"
    assert autorun.scan("allocator", "x\n" + dd, None)[0].startswith("drawdown flag: DRAWDOWN")
    assert autorun.scan("allocator", "fine", None) == []


def test_check_finds_missing_weekday_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    hb = [  # Mon 5 Oct: two event runs + allocator; Tue 6 Oct: one event run; Wed 7 Oct: none
        {"job": "events", "mode": "dry", "start": "2026-10-05T12:45:00+00:00", "rc": 0},
        {"job": "events", "mode": "dry", "start": "2026-10-05T22:30:00+00:00", "rc": 0},
        {"job": "allocator", "mode": "dry", "start": "2026-10-05T22:00:00+00:00", "rc": 0},
        {"job": "events", "mode": "dry", "start": "2026-10-06T12:45:00+00:00", "rc": 0},
        {"job": "events", "mode": "dry", "start": "2026-10-06T22:30:00+00:00", "rc": 1},  # failed: does not count
    ]
    (tmp_path / "heartbeat.jsonl").write_text("\n".join(json.dumps(r) for r in hb) + "\n")
    gaps = autorun.check(today=date(2026, 10, 8))
    assert gaps == ["2026-10-07: 0 of 2 event runs", "2026-10-06: 1 of 2 event runs"]


def test_live_check_finds_missing_first_day_without_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    (tmp_path / "AUTORUN_MODE").write_text("live\n")
    assert autorun.check(today=date(2026, 10, 1)) == ["2026-09-30: 0 of 2 event runs"]


def _hb(job, day, hour, rc=0):
    return {"job": job, "mode": "dry", "start": f"2026-{day}T{hour}:00:00+00:00", "rc": rc}


def test_go_live_switches_only_after_a_clean_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    monkeypatch.setattr(autorun, "alert", lambda *a: None)
    days = ["09-28", "09-29", "09-30", "10-01", "10-02"]
    clean = [_hb("allocator", "09-28", "22")] + [_hb("events", d, h) for d in days for h in ("12", "22")]
    (tmp_path / "heartbeat.jsonl").write_text("\n".join(json.dumps(r) for r in clean) + "\n")
    assert autorun.go_live(date(2026, 10, 1)) is None            # too early: nothing happens
    assert autorun.mode() == "dry"
    from app.forward.ledger import Ledger

    events = tmp_path / "events_autodry" / "ledger.jsonl"
    events.parent.mkdir()
    Ledger(events).append("run", as_of="2026-10-02T22:30:00+00:00", new=0)
    allocator = tmp_path / "allocator_autodry" / "ledger.jsonl"
    allocator.parent.mkdir()
    allocator.write_text(json.dumps({"books": {"master": {}}, "price_sources": {"SPY": "yahoo"}}) + "\n")
    assert autorun.go_live_reasons(clean, events) == []
    msg = autorun.go_live(date(2026, 10, 2))
    assert msg.startswith("switched to LIVE") and autorun.mode() == "live"
    assert autorun.go_live(date(2026, 10, 3)) is None            # already live: nothing to do


def test_go_live_stays_dry_with_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    bad = [_hb("events", d, h, rc=1 if d == "10-02" else 0) for d in ["09-29", "09-30", "10-01", "10-02"]
           for h in ("12", "22")]
    (tmp_path / "heartbeat.jsonl").write_text("\n".join(json.dumps(r) for r in bad) + "\n")
    msg = autorun.go_live(date(2026, 10, 2))
    assert msg.startswith("still in DRY mode") and autorun.mode() == "dry"
    assert "dry event ledger missing" in msg and "dry allocator ledger missing" in msg
    for part in ("good dry event runs", "2 failed dry runs", "last two", "no good dry allocator run"):
        assert part in msg


def test_review_runs_the_monthly_loop_only_live_and_collect_follows_events():
    assert not any("monthly" in c for c in autorun.commands("review", "dry"))
    assert any("monthly" in c for c in autorun.commands("review", "live"))
    assert autorun.commands("events", "live")[-1][-1] == "collect"
    assert autorun.commands("events", "live")[2][1] == "scripts/ai_picks.py"
    assert autorun.commands("events", "live")[3][1] == "scripts/guidance_shadow.py"
    assert autorun.commands("events", "live")[4][1] == "scripts/net_read_shadow.py"
    ev = [c[1] for c in autorun.commands("events", "dry")]
    assert ev.index("scripts/themes.py") == ev.index("scripts/longterm_picks.py") + 1
    assert autorun.commands("events", "dry")[2][-1] == "results/forward/ai_picks_autodry"
    assert autorun.commands("events", "dry")[3][-1] == "results/forward/guidance_shadow_autodry"
    assert autorun.commands("events", "dry")[4][-1] == "results/forward/events_autodry"
    assert autorun.commands("learn", "dry") == []
    assert autorun.scan("review", "x\nLEARN ALERT: signal a promoted", None) == ["signal a promoted"]


def test_step_runs_without_the_inhibitor_when_logind_refuses(monkeypatch):
    import subprocess

    import autorun
    calls = []

    def fake(args, **kw):
        calls.append(args)
        if args[0] == "systemd-inhibit":
            return subprocess.CompletedProcess(args, 1, "", "Failed to inhibit: Access denied as the requested ...")
        return subprocess.CompletedProcess(args, 0, "ok\n", "")
    monkeypatch.setattr(autorun.subprocess, "run", fake)
    assert not autorun.can_inhibit()
    r = autorun.step(["python", "x.py"], inhibit=False)
    assert r.returncode == 0 and "ran without it" in r.stdout and calls[-1] == ["python", "x.py"]


def test_step_retries_only_a_denied_inhibitor(monkeypatch):
    import subprocess

    calls = []

    def fake(args, **kw):
        calls.append(args)
        if args[0] == "systemd-inhibit":
            return subprocess.CompletedProcess(args, 1, "", autorun.INHIBIT_DENIED + "\n")
        return subprocess.CompletedProcess(args, 0, "child ran\n", "")

    monkeypatch.setattr(autorun.subprocess, "run", fake)
    r = autorun.step(["python", "x.py"], inhibit=True)
    assert r.returncode == 0 and "child ran" in r.stdout
    assert "LEARN ALERT: sleep inhibitor refused" in r.stdout
    assert len(calls) == 2 and calls[-1] == ["python", "x.py"]


def test_step_keeps_a_real_failure(monkeypatch):
    import subprocess

    import autorun
    calls = []

    def fake(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "Failed to inhibit: looks similar but is the job's own error")
    monkeypatch.setattr(autorun.subprocess, "run", fake)
    assert autorun.step(["python", "x.py"], inhibit=True).returncode == 1 and len(calls) == 1  # never re-run


def test_wait_online_retries_until_the_network_is_up(monkeypatch):
    calls = []

    class Sock:
        def close(self):
            pass

    def conn(addr, timeout):
        calls.append(addr)
        if len(calls) < 3:
            raise OSError("network unreachable")
        return Sock()
    monkeypatch.setattr(autorun.socket, "create_connection", conn)
    assert autorun.wait_online(tries=5, pause=0) and len(calls) == 3
    calls.clear()
    monkeypatch.setattr(autorun.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert not autorun.wait_online(tries=2, pause=0)


def test_failed_required_step_stops_following_steps(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(autorun, "FWD", tmp_path)
    monkeypatch.setattr(autorun, "BACKEND", tmp_path)
    monkeypatch.setattr(autorun, "wait_online", lambda: True)
    monkeypatch.setattr(autorun, "can_inhibit", lambda: False)
    steps = [["py", "scripts/broker_sync.py"], ["py", "scripts/forward_events.py"], ["py", "scripts/ai_picks.py"],
             ["py", "scripts/longterm_picks.py"], ["py", "scripts/themes.py"], ["py", "scripts/learn_loop.py"]]
    monkeypatch.setattr(autorun, "commands", lambda job, mode: steps)
    monkeypatch.setattr(autorun, "post_run", lambda job: None)
    monkeypatch.setattr(autorun, "alert", lambda *a: None)
    called = []
    failing = {"scripts/broker_sync.py", "scripts/forward_events.py"}

    def fake_step(cmd, inhibit):
        called.append(cmd[1])
        return subprocess.CompletedProcess(cmd, 1 if cmd[1] in failing else 0, "", "")

    monkeypatch.setattr(autorun, "step", fake_step)
    assert autorun.run("events") == 1
    # a broker failure blocks nothing; an event-runner failure skips only its readers
    assert called == ["scripts/broker_sync.py", "scripts/forward_events.py", "scripts/longterm_picks.py",
                      "scripts/themes.py"]


def test_disk_low_alerts_only_below_the_floor(tmp_path):
    assert autorun.disk_low(tmp_path, min_free_gb=0.0) is None
    msg = autorun.disk_low(tmp_path, min_free_gb=1e9)
    assert msg is not None and msg.startswith("disk space low")


def _run_with(tmp_path, monkeypatch, steps, results):
    import subprocess
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    monkeypatch.setattr(autorun, "BACKEND", tmp_path)
    monkeypatch.setattr(autorun, "wait_online", lambda: True)
    monkeypatch.setattr(autorun, "can_inhibit", lambda: False)
    monkeypatch.setattr(autorun, "commands", lambda job, mode: steps)
    monkeypatch.setattr(autorun, "alert", lambda *a: None)
    monkeypatch.setattr(autorun, "post_run", lambda job: None)
    monkeypatch.setattr(autorun, "RETRY_WAIT", 0)
    calls = []

    def fake_step(cmd, inhibit):
        calls.append(cmd[1])
        rc, err = results.pop(0)
        return subprocess.CompletedProcess(cmd, rc, "", err)
    monkeypatch.setattr(autorun, "step", fake_step)
    return autorun.run("events"), calls


def test_a_transient_network_failure_is_retried_once(tmp_path, monkeypatch):
    steps = [["py", "scripts/forward_events.py"]]
    rc, calls = _run_with(tmp_path, monkeypatch, steps, [(1, "URLError: Temporary failure in name resolution"),
                                                          (0, "")])
    assert rc == 0 and calls == ["scripts/forward_events.py"] * 2


def test_a_real_failure_or_unsafe_step_is_not_retried(tmp_path, monkeypatch):
    rc, calls = _run_with(tmp_path, monkeypatch, [["py", "scripts/forward_events.py"]], [(1, "KeyError: 'eps'")])
    assert rc == 1 and calls == ["scripts/forward_events.py"]
    rc, calls = _run_with(tmp_path, monkeypatch, [["py", "scripts/learn_loop.py"]], [(1, "HTTP 503")])
    assert rc == 1 and calls == ["scripts/learn_loop.py"]


def test_consensus_shadow_runs_right_after_the_labels_in_live_event_runs_only():
    live = [c[1] for c in autorun.commands("events", "live")]
    i = live.index("scripts/consensus_shadow.py")
    assert live[i - 1] == "scripts/net_read_shadow.py" and live[i + 1] == "scripts/longterm_picks.py"
    assert "scripts/consensus_shadow.py" not in [c[1] for c in autorun.commands("events", "dry")]
    for job in ("allocator", "review"):
        assert "scripts/consensus_shadow.py" not in [c[1] for c in autorun.commands(job, "live")]


def test_a_failing_consensus_shadow_never_changes_the_runs_result(tmp_path, monkeypatch):
    steps = [["py", "scripts/forward_events.py"], ["py", "scripts/consensus_shadow.py"], ["py", "scripts/themes.py"]]
    rc, calls = _run_with(tmp_path, monkeypatch, steps, [(0, ""), (1, "boom"), (0, "")])
    assert rc == 0 and calls == ["scripts/forward_events.py", "scripts/consensus_shadow.py", "scripts/themes.py"]
    assert "consensus_shadow.py failed; the run's result is not affected" in next(tmp_path.glob("logs/*.log")).read_text()
    # and it is skipped, like the other readers, when the event runner itself failed
    rc, calls = _run_with(tmp_path, monkeypatch, steps, [(1, "KeyError"), (0, "")])
    assert rc == 1 and calls == ["scripts/forward_events.py", "scripts/themes.py"]


def test_an_event_run_that_only_alerted_counts_as_run_and_is_pushed(tmp_path, monkeypatch):
    """30 Sep / 1 Oct 2026: the event runner finished, then ai_picks raised a broker alert (exit 1). The decisions
    were made, so it is not a missed run, and its ledger still gets its outside timestamp."""
    monkeypatch.setattr(autorun, "FWD", tmp_path)
    (tmp_path / "AUTORUN_MODE").write_text("live\n")
    hb = [{"job": "events", "mode": "live", "start": "2026-09-30T12:45:00+00:00", "rc": 0, "missed_total": 0},
          {"job": "events", "mode": "live", "start": "2026-09-30T22:30:00+00:00", "rc": 1, "missed_total": 0}]
    (tmp_path / "heartbeat.jsonl").write_text("\n".join(json.dumps(r) for r in hb) + '\n{"job": "events", "mo')  # torn
    assert autorun.check(today=date(2026, 10, 1)) == []
    assert len(autorun.heartbeats()) == 2  # the torn line is skipped, not fatal
    autorun.append("heartbeat.jsonl", {"job": "check", "mode": "live", "rc": 0})
    assert autorun.heartbeats()[-1]["job"] == "check"  # and the next record starts on its own line

    scoreboard = '{\n "decisions": 2,\n "missed": 0\n}\n'
    steps = [["py", "scripts/forward_events.py"], ["py", "scripts/ai_picks.py"]]
    results = {"scripts/forward_events.py": (0, scoreboard), "scripts/ai_picks.py": (1, "BROKER ALERT: x\n")}
    monkeypatch.setattr(autorun, "BACKEND", tmp_path)
    monkeypatch.setattr(autorun, "wait_online", lambda: True)
    monkeypatch.setattr(autorun, "can_inhibit", lambda: False)
    monkeypatch.setattr(autorun, "commands", lambda job, mode: steps)
    monkeypatch.setattr(autorun, "alert", lambda *a: None)
    monkeypatch.setattr(autorun, "post_run", lambda job: None)
    monkeypatch.setattr(autorun, "step", lambda cmd, inhibit: subprocess.CompletedProcess(cmd, *results[cmd[1]], ""))
    pushed = []
    monkeypatch.setattr(autorun, "push", pushed.append)
    assert autorun.run("events") == 1 and pushed == ["events"]
    results["scripts/forward_events.py"] = (1, "KeyError\n")  # the runner itself failed: nothing is pushed
    assert autorun.run("events") == 1 and pushed == ["events"]


def test_a_hung_step_and_a_missing_notifier_do_not_kill_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(autorun, "FWD", tmp_path)

    def hang(cmd, inhibit):
        raise subprocess.TimeoutExpired(cmd, 6 * 3600)
    monkeypatch.setattr(autorun, "step", hang)
    r = autorun.safe_step(["py", "scripts/forward_events.py"], False)
    assert r.returncode == 124 and "TimeoutExpired" in r.stderr

    def no_notifier(cmd, **kw):
        raise FileNotFoundError("notify-send")
    monkeypatch.setattr(autorun.subprocess, "run", no_notifier)
    monkeypatch.setattr(autorun, "ntfy_topic", lambda: "")
    autorun.alert("events", "something")  # must not raise
    assert json.loads((tmp_path / "alerts.jsonl").read_text())["msg"] == "something"


def test_an_empty_yahoo_answer_is_retried_once(tmp_path, monkeypatch):
    """Code review, 1 Oct 2026: when Yahoo returns no prices the event runner stops with one of these messages; a
    retry 90 s later saves the morning's decisions (the runner skips anything already in its ledger)."""
    for msg in ("ValueError: No objects to concatenate", "ValueError: event data check: no SPY closing prices",
                "yfinance.exceptions.YFRateLimitError: Too Many Requests. Rate limited."):
        rc, calls = _run_with(tmp_path, monkeypatch, [["py", "scripts/forward_events.py"]], [(1, msg), (0, "")])
        assert rc == 0 and calls == ["scripts/forward_events.py"] * 2


def test_m1_shadow_is_a_side_step_of_live_event_runs_only():
    live = [c[1] for c in autorun.commands("events", "live")]
    assert live.count("scripts/m1_shadow.py") == 1 and "scripts/m1_shadow.py" in autorun.SIDE_STEPS
    assert live.index("scripts/m1_shadow.py") > live.index("scripts/forward_events.py")
    assert "scripts/m1_shadow.py" not in [c[1] for c in autorun.commands("events", "dry")]
    assert "scripts/m1_shadow.py" not in autorun.EVENT_READERS  # it reads no event file: runs even if the runner failed
