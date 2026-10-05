import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from day_calls_shadow import bootstrap, calls, entry_day, score


def rec(t, at, label, support="event", status="decided"):
    return {"ticker": t, "decided_at": at, "status": status,
            "call_support": {"day": {"label": label, "support": support}}}


def test_entry_day_is_the_next_open_after_the_decision():
    assert entry_day(datetime(2026, 10, 5, 12, 0, tzinfo=UTC)) == date(2026, 10, 5)   # 08:00 NY, Monday
    assert entry_day(datetime(2026, 10, 5, 14, 0, tzinfo=UTC)) == date(2026, 10, 6)   # 10:00 NY: next day
    assert entry_day(datetime(2026, 10, 3, 15, 0, tzinfo=UTC)) == date(2026, 10, 5)   # Saturday -> Monday


def test_one_call_per_stock_and_day_latest_research_feasible_only():
    rs = [rec("AAA", "2026-10-04T20:00:00+00:00", "4"), rec("AAA", "2026-10-05T01:00:00+00:00", "2", "quoted"),
          rec("BBB", "2026-10-04T20:00:00+00:00", "5"), rec("CCC", "2026-10-04T20:00:00+00:00", "1"),
          rec("DDD", "2026-10-04T20:00:00+00:00", "4", status="judge_failed")]
    out = calls(rs, {"AAA", "CCC", "DDD"})
    assert set(out) == {"AAA:2026-10-05", "CCC:2026-10-05"}
    assert out["AAA:2026-10-05"]["side"] == -1 and out["AAA:2026-10-05"]["support"] == "quoted"
    assert out["CCC:2026-10-05"]["strong"] and out["CCC:2026-10-05"]["side"] == -1


def test_score_is_hedged_and_net_of_cost_and_the_interval_brackets_the_mean():
    assert round(score({"side": 1}, 100, 102, 100, 101), 6) == round(0.02 - 0.01 - 0.0012, 6)
    assert round(score({"side": -1}, 100, 102, 100, 101), 6) == round(-0.01 - 0.0012, 6)
    s = [{"entry_day": f"d{i % 30}", "net": 0.001 * ((i % 7) - 3)} for i in range(300)]
    lo, hi = bootstrap(s, n=500)
    assert lo < sum(x["net"] for x in s) / len(s) < hi
