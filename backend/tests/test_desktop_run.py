"""App actions on scratch paths; no model, broker, market data or trial registry calls."""
import fcntl
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import desktop_run as runner


def setup(tmp_path, monkeypatch):
    fwd = tmp_path / "results" / "forward"
    fwd.mkdir(parents=True)
    (fwd / "AUTORUN_MODE").write_text("live\n")
    job = tmp_path / "results" / "desktop_runs" / "fake"
    job.mkdir(parents=True)
    (job / "status.json").write_text(json.dumps({"id": "fake", "action": "paper_ai"}))
    monkeypatch.setattr(runner, "next_slot", lambda now: now.replace(year=now.year + 1))
    return fwd, job


def test_shared_lock_blocks_every_action_without_running_a_child(tmp_path, monkeypatch):
    fwd, job = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "execute", lambda *a: (_ for _ in ()).throw(AssertionError("child started")))
    with (fwd / "autorun.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert runner.run("paper_ai", job, "node", tmp_path) == 2
        assert runner.run("backend_tests", job, "node", tmp_path) == 2
    assert json.loads((job / "status.json").read_text())["state"] == "blocked"


def test_failure_stops_before_paper_submission_and_preserves_steps(tmp_path, monkeypatch):
    fwd, job = setup(tmp_path, monkeypatch)
    seen = []

    def fail(cmd, *args):
        seen.append(cmd)
        return 1, False

    monkeypatch.setattr(runner, "execute", fail)
    assert runner.run("paper_ai", job, "node", tmp_path) == 1
    assert len(seen) == 1 and "scripts/forward_events.py" in seen[0]
    status = json.loads((job / "status.json").read_text())
    assert status["state"] == "failed" and status["steps"][0]["code"] == 1
    assert not (fwd / "heartbeat.jsonl").exists()
    assert not (fwd / "running.json").exists()


def test_halt_between_scoring_and_submission_stops_orders(tmp_path, monkeypatch):
    fwd, job = setup(tmp_path, monkeypatch)
    seen = []

    def halt(cmd, *args):
        seen.append(cmd)
        (fwd / "HALT").write_text("{}")
        return 0, False

    monkeypatch.setattr(runner, "execute", halt)
    assert runner.run("paper_ai", job, "node", tmp_path) == 2
    assert len(seen) == 1
    assert json.loads((job / "status.json").read_text())["state"] == "blocked"


def test_warning_is_not_a_clean_run(tmp_path, monkeypatch):
    _, job = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "execute", lambda *a: (0, True))
    assert runner.run("paper_ai", job, "node", tmp_path) == 0
    status = json.loads((job / "status.json").read_text())
    assert status["state"] == "warning" and len(status["steps"]) == 6
    assert status["identity"] == runner.identity(status["pid"])


def test_schedule_and_mode_preflight(tmp_path):
    fwd = tmp_path
    now = datetime(2026, 10, 5, 12, 30, tzinfo=UTC)  # 15 min before Monday earnings run
    (fwd / "AUTORUN_MODE").write_text("live")
    assert "due soon" in runner.preflight("paper_ai", fwd, now)
    assert runner.next_slot(now) == datetime(2026, 10, 5, 12, 45, tzinfo=UTC)
    (fwd / "AUTORUN_MODE").write_text("dry")
    assert "mode" in runner.preflight("paper_ai", fwd, now)
    (fwd / "AUTORUN_MODE").write_text("live")
    (fwd / "HALT").write_text("{}")
    later = datetime(2026, 10, 5, 15, tzinfo=UTC)
    assert "halted" in runner.preflight("paper_ai", fwd, later)
    assert runner.preflight("paper_sync", fwd, later) is None
    # Timer times remain Eastern after the November daylight saving change.
    assert runner.next_slot(datetime(2026, 11, 2, 13, tzinfo=UTC)).hour == 13


def test_execution_streams_and_detects_zero_exit_broker_alert(tmp_path):
    log = tmp_path / "output.log"
    with (tmp_path / "lock").open("a") as lock:
        rc, warning = runner.execute([sys.executable, "-c", "print('BROKER ALERT: rejected hedge')"],
                                     10, log, lock.fileno())
    assert rc == 1 and warning
    assert "rejected hedge" in log.read_text()


def test_execution_timeout_finishes_with_failure(tmp_path):
    with (tmp_path / "lock").open("a") as lock:
        rc, _ = runner.execute([sys.executable, "-c", "import time; time.sleep(30)"],
                               0.05, tmp_path / "log", lock.fileno())
    assert rc == 124


def test_zero_skipped_tests_is_a_clean_result(tmp_path):
    with (tmp_path / "lock").open("a") as lock:
        rc, warning = runner.execute([sys.executable, "-c", "print('skipped 0')"],
                                     10, tmp_path / "log", lock.fileno())
    assert rc == 0 and not warning


def test_actions_never_invoke_trials_or_scheduled_runs():
    for action in runner.ACTIONS:
        text = " ".join(" ".join(cmd) for _, cmd in runner.commands(action, "node"))
        assert "autorun.py" not in text and "daytrade_test.py" not in text
        assert "forward_allocator.py" not in text
        for _, cmd in runner.commands(action, "node"):
            if "scripts/learn_loop.py" in cmd:
                assert "collect" in cmd and "monthly" not in cmd


def test_paper_ai_explicitly_selects_jan_research() -> None:
    commands = runner.commands("paper_ai", "node")
    assert "--jan-research" in commands[0][1]
    assert "Jan" in commands[0][0] and "Bonsai" in commands[0][0]
    assert not any("--jan-research" in command for _, command in runner.commands("paper_sync", "node"))


def test_sync_failure_still_snapshots_without_another_submission(tmp_path, monkeypatch):
    _, job = setup(tmp_path, monkeypatch)
    seen = []
    def failure(cmd, *_):
        seen.append(cmd)
        return (1, False) if len(seen) == 1 else (0, False)
    monkeypatch.setattr(runner, 'execute', failure)
    assert runner.run('paper_sync', job, 'node', tmp_path) == 1
    assert len(seen) == 2
    assert 'scripts/account_view.py' in seen[1]
    assert not any('scripts/ai_picks.py' in cmd for cmd in seen)
