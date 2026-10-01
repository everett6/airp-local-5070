"""Research queue (scripts/research_queue.py): scheduling rules, with a fake queue and no processes started."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_queue as rq

LA = rq.LOCAL


def setup(tmp_path, monkeypatch, jobs, lines=0):
    monkeypatch.setattr(rq, "BACKEND", tmp_path)
    monkeypatch.setattr(rq, "QDIR", tmp_path / "q")
    monkeypatch.setattr(rq, "QUEUE", tmp_path / "q" / "queue.json")
    monkeypatch.setattr(rq, "running", lambda m: [])
    monkeypatch.setattr(rq, "gpu_busy", lambda: False)
    monkeypatch.setattr(rq, "free_ram_gb", lambda: 20.0)
    (tmp_path / "log.jsonl").write_text("".join(json.dumps({"accession": str(i), "status": "ok"}) + "\n"
                                                for i in range(lines)))
    rq.save(jobs)


def job(name, kind, target=3, **kw):
    return {"name": name, "kind": kind, "cmd": ["true"], "match": name,
            "done": {"file": "log.jsonl", "target": target, "status_in": ["ok"]}, **kw}


def test_net_runs_any_time_gpu_only_at_night(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [job("net1", "net"), job("gpu1", "gpu")])
    assert rq.tick(datetime(2026, 10, 1, 12, 0, tzinfo=LA), dry=True) == ["start net1"]
    assert rq.tick(datetime(2026, 10, 1, 19, 0, tzinfo=LA), dry=True) == ["start net1", "start gpu1"]
    assert rq.tick(datetime(2026, 10, 2, 5, 0, tzinfo=LA), dry=True) == ["start net1"]  # after 04:30: no new GPU start


def test_done_paused_requirements_and_ram(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [job("a", "net", target=2), job("b", "gpu", requires=["a"]),
                                  job("c", "net", paused=True)], lines=1)
    assert rq.tick(datetime(2026, 10, 1, 20, 0, tzinfo=LA), dry=True) == ["start a"]  # b waits for a; c paused
    (tmp_path / "log.jsonl").write_text("".join(json.dumps({"accession": str(i), "status": "ok"}) + "\n"
                                                for i in range(2)))
    monkeypatch.setattr(rq, "free_ram_gb", lambda: 8.0)
    assert rq.tick(datetime(2026, 10, 1, 20, 0, tzinfo=LA), dry=True) == ["b: GPU busy or RAM low, next tick"]


def test_gate_waits_for_approval_and_notifies_once(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [job("a", "net", target=0), {"name": "g", "kind": "gate", "requires": ["a"]},
                                  job("after", "net", requires=["g"])])
    sent = []
    monkeypatch.setattr(rq, "notify", lambda t, m: sent.append(m))
    rq.tick(datetime(2026, 10, 1, 20, 0, tzinfo=LA))
    rq.tick(datetime(2026, 10, 1, 21, 0, tzinfo=LA))
    assert len(sent) == 1 and not any(s["name"] == "after" and s["running"] for s in rq.status())
    rq.set_flag("g", "approved", True)
    monkeypatch.setattr(rq, "start", lambda j, now: None)
    assert "start after" in rq.tick(datetime(2026, 10, 1, 22, 0, tzinfo=LA))


def test_gpu_stop_time_is_before_the_live_run():
    assert rq.seconds_to_stop(datetime(2026, 10, 1, 23, 25, tzinfo=LA)) == 6 * 3600
    assert rq.seconds_to_stop(datetime(2026, 10, 2, 5, 0, tzinfo=LA)) == 25 * 60


def test_jobs_start_outside_the_callers_control_group(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rq.shutil, "which", lambda _: "/usr/bin/systemd-run")
    cmd = rq.detached(["sleep", "1"], "x", tmp_path / "x.log")
    assert cmd is not None and cmd[:2] == ["systemd-run", "--user"] and "--scope" not in cmd
    assert "--unit=airp-research-x" in cmd and cmd[-3:] == ["--", "sleep", "1"]
    monkeypatch.setattr(rq.shutil, "which", lambda _: None)
    assert rq.detached(["sleep", "1"], "x", tmp_path / "x.log") is None
