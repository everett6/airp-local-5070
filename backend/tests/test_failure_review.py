import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from failure_review import cause, from_ledger, from_records


def test_cause_groups_the_same_failure():
    a = cause("no archived news page for ITW in the 10 days before the decision")
    b = cause("no archived news page for MMM in the 7 days before the decision")
    assert a == b
    assert cause("GET https://x.org/a?b=1 timed out after 15.0 s") == "GET <url> timed out after # s"
    assert cause("") == "unknown"


def test_records_and_ledgers(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    (run / "a.json").write_text(json.dumps({
        "tool_log": [{"tool": "news_as_of", "ok": False, "error": "timed out"}, {"tool": "x", "ok": True}],
        "parse_failures": 2, "brief": {"dropped": {"no_source": 1, "number_not_in_evidence": 3}}}))
    rows = from_records(run)
    assert len(rows) == 1 + 2 + 1 + 3
    assert {r["stage"] for r in rows} == {"tool:news_as_of", "model_reply", "brief_check"}
    led = tmp_path / "events" / "ledger.jsonl"
    led.parent.mkdir()
    led.write_text(json.dumps({"type": "missed", "accession": "q", "reason": "late"}) + "\n"
                   + json.dumps({"type": "decision", "accession": "r"}) + "\n")
    assert from_ledger(led) == [{"run": "events", "id": "q", "stage": "forward", "cause": "late"}]
