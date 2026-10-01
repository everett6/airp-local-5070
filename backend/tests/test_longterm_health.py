"""Synthetic monthly cohort readiness checks; no downloads or model calls."""
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import longterm_picks as L


def cohort(month="2026-10", n=10):
    return {"type": "cohort", "month": month, "made_on": f"{month}-01",
            "tickers": [f"T{i}" for i in range(n)]}


def test_missing_first_cohort_is_visible_only_when_due():
    before = datetime(2026, 10, 1, 19, tzinfo=UTC)
    after = datetime(2026, 10, 1, 22, tzinfo=UTC)
    assert L.status([], before)["missing_months"] == []
    assert L.status([], after)["missing_months"] == ["2026-10"]
    report = L.status([cohort()], after)
    assert report["missing_months"] == [] and report["pending"] == ["2026-10"]
    assert not report["ready_to_judge"]


def test_incomplete_cohort_and_result_cannot_be_judged():
    now = datetime(2027, 2, 1, 22, tzinfo=UTC)
    report = L.status([cohort(n=9), {"type": "result", "month": "2026-10", "priced": 9,
                                       "excess_net": 0.02}], now)
    assert any("distinct picks" in x for x in report["issues"])
    assert any("invalid result" in x for x in report["issues"])
    assert not report["ready_to_judge"]


def test_overdue_unscored_cohort_is_flagged():
    report = L.status([cohort()], datetime(2027, 2, 1, 22, tzinfo=UTC))
    assert report["overdue_results"] == ["2026-10"]
