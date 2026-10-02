"""Stage timings and decision latency from the ledger (scripts/throughput.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import throughput as T


def dec(t: str, accepted: str, as_of: str, written: str) -> dict:
    return {"type": "decision", "ticker": t, "accession": t, "accepted_utc": accepted, "as_of": as_of,
            "written_at": written, "entry_deadline": "2026-10-02T09:30:00-04:00"}


def test_latency_slack_and_stage_times() -> None:
    recs = [
        dec("AAA", "2026-10-01T20:10:00", "2026-10-01T22:30:00+00:00", "2026-10-01T22:33:00+00:00"),
        {"type": "run", "as_of": "2026-10-01T22:30:00+00:00", "new": 1, "gpu": True,
         "timing": {"discovery": 100.0, "judge": 8.0, "total": 120.0}},
        dec("BBB", "2026-10-02T11:00:00", "2026-10-02T12:45:00+00:00", "2026-10-02T13:25:00+00:00"),  # 5 min to spare
        {"type": "missed", "accession": "c", "reason": "decided after the entry open: never backfilled"},
        {"type": "run", "as_of": "2026-10-02T12:45:00+00:00", "new": 40, "gpu": True,
         "timing": {"discovery": 110.0, "judge": 900.0, "total": 2400.0}},
        {"type": "run", "as_of": "2026-09-30T12:45:00+00:00", "new": 1},  # a run from before timings were recorded
    ]
    r = T.report(recs)
    assert r["runs_timed"] == 2 and r["stage_seconds"]["judge"] == {"median": 454.0, "worst": 900.0, "n": 2}
    assert r["busiest_run"]["new"] == 40 and r["busiest_run"]["timing"]["total"] == 2400.0
    d = r["per_decision_seconds"]
    assert d["queue_wait"]["worst"] == 8400.0 and d["work"]["worst"] == 2400.0  # AAA waited 2h20 for the evening run
    assert d["latency"]["worst"] == 8700.0 and d["latency"]["median"] == 8640.0 and d["latency"]["n"] == 2
    assert r["slack_before_open_seconds"]["least"] == 300.0 and r["missed_because_late"] == 1
    assert [t["ticker"] for t in r["tight"]] == ["BBB"]
    empty = T.report([])
    assert empty["stage_seconds"] == {} and empty["slack_before_open_seconds"] is None and empty["tight"] == []
