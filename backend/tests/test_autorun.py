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
