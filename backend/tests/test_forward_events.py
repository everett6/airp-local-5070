import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from forward_events import entry_deadline, score

from app.forward.schedule import NY


def test_after_close_release_enters_at_next_open():
    # 16:05 ET Tuesday (20:05 UTC in summer) -> Wednesday 09:30 ET
    assert entry_deadline("2026-09-22T20:05:00") == datetime(2026, 9, 23, 9, 30, tzinfo=NY)


def test_pre_open_release_enters_the_same_morning():
    # 07:00 ET Tuesday -> Tuesday 09:30 ET
    assert entry_deadline("2026-09-22T11:00:00") == datetime(2026, 9, 22, 9, 30, tzinfo=NY)


def test_friday_evening_and_weekend_releases_enter_on_monday():
    assert entry_deadline("2026-09-25T21:00:00") == datetime(2026, 9, 28, 9, 30, tzinfo=NY)
    assert entry_deadline("2026-09-26T15:00:00") == datetime(2026, 9, 28, 9, 30, tzinfo=NY)


def test_score_counts_each_source_separately_and_ignores_missed():
    recs = [{"type": "decision", "accession": f"a{i}", "source": "bonsai", "logodds": i, "on_time": True}
            for i in range(12)]
    recs += [{"type": "outcome", "accession": f"a{i}", "fwd5": i / 100} for i in range(12)]
    recs += [{"type": "missed", "accession": "m1"}, {"type": "decision", "accession": "l1", "source": "lite",
                                                      "logodds": 0.1, "on_time": True}]
    s = score(recs)
    assert s["bonsai_ic"] == 1.0 and s["bonsai_n"] == 12
    assert "lite_ic" not in s  # fewer than 10 scored lite decisions
    assert s["missed"] == 1 and s["on_time"] == 13
