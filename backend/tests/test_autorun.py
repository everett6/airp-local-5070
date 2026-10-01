"""Autonomy runner: mode file, dry-run folders, alert scanning, missed-run check (no subprocesses run here)."""
import json
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


def test_post_run_records_the_consensus_shadow_after_live_event_runs_only(monkeypatch):
    seen = []
    monkeypatch.setattr(autorun.subprocess, "run", lambda cmd, **kw: seen.append(cmd[1]))
    monkeypatch.setattr(autorun, "mode", lambda: "live")
    autorun.post_run("events")
    assert "scripts/consensus_shadow.py" in seen and seen[0] == "scripts/research_queue.py"
    seen.clear()
    autorun.post_run("check")
    assert "scripts/consensus_shadow.py" not in seen
    monkeypatch.setattr(autorun, "mode", lambda: "dry")
    seen.clear()
    autorun.post_run("events")
    assert "scripts/consensus_shadow.py" not in seen
