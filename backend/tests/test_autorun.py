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
    assert autorun.go_live_reasons(clean, tmp_path / "none.jsonl") == []
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
    for part in ("good dry event runs", "2 failed dry runs", "last two", "no good dry allocator run"):
        assert part in msg


def test_review_runs_the_monthly_loop_only_live_and_collect_follows_events():
    assert not any("monthly" in c for c in autorun.commands("review", "dry"))
    assert any("monthly" in c for c in autorun.commands("review", "live"))
    assert autorun.commands("events", "live")[-1][-1] == "collect"
    assert autorun.commands("events", "live")[2][1] == "scripts/net_read_shadow.py"
    ev = [c[1] for c in autorun.commands("events", "dry")]
    assert ev.index("scripts/themes.py") == ev.index("scripts/longterm_picks.py") + 1
    assert autorun.commands("events", "dry")[2][-1] == "results/forward/events_autodry"
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


def test_step_keeps_a_real_failure(monkeypatch):
    import subprocess

    import autorun
    calls = []

    def fake(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "Failed to inhibit: looks similar but is the job's own error")
    monkeypatch.setattr(autorun.subprocess, "run", fake)
    assert autorun.step(["python", "x.py"], inhibit=True).returncode == 1 and len(calls) == 1  # never re-run
