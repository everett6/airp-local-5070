"""Daily digest text (scripts/digest.py), synthetic forward folder."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from digest import build


def w(p: Path, recs: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_digest_summarises_runs_decisions_picks_alerts_and_research(tmp_path):
    (tmp_path / "AUTORUN_MODE").write_text("live\n")
    w(tmp_path / "heartbeat.jsonl", [{"job": "events", "start": "2026-09-30T12:45", "rc": 0},
                                      {"job": "events", "start": "2026-09-30T22:30", "rc": 1},
                                      {"job": "events", "start": "2026-09-29T22:30", "rc": 0}])
    w(tmp_path / "events" / "ledger.jsonl", [{"type": "decision", "as_of": "2026-09-30T12:45", "ticker": "JBL",
                                              "logodds": 4.14}, {"type": "missed", "as_of": "2026-09-30T12:45"}])
    w(tmp_path / "alerts.jsonl", [{"at": "2026-09-30T22:31", "job": "events", "msg": "failed (exit 1)"}])
    (tmp_path / "ai_picks").mkdir()
    (tmp_path / "ai_picks" / "book.json").write_text(json.dumps({"equity": 10000, "pairs": [{"status": "open"}]}))
    research = [{"name": "warm", "kind": "net", "done": False, "running": True, "progress": [63, 2851], "requires": []},
                {"name": "gate", "kind": "gate", "done": False, "running": False, "progress": [0, 0],
                 "requires": ["warm"]}]
    text = build(date(2026, 9, 30), tmp_path, research)
    assert "airp 2026-09-30 (live)" in text and "runs: 1/2 clean, FAILED: events" in text
    assert "decisions: 1 on time, 1 missed; top JBL +4.1" in text and "pairs {'open': 1}" in text
    assert "alerts: 1 (latest: failed (exit 1))" in text and "warm 63/2851 running" in text
    assert "WAITING" not in text  # the gate's requirement is not done yet


def test_a_run_that_finished_with_an_alert_is_not_called_failed(tmp_path):
    w(tmp_path / "heartbeat.jsonl", [{"job": "events", "start": "2026-10-01T12:45", "rc": 1, "missed_total": 0},
                                      {"job": "events", "start": "2026-10-01T22:30", "rc": 0, "missed_total": 0}])
    assert "runs: 1/2 clean, 1 finished with an alert" in build(date(2026, 10, 1), tmp_path)
    assert "FAILED" not in build(date(2026, 10, 1), tmp_path)
