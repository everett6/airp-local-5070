import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import longterm_picks as L


def test_due_first_weekday_after_16_et_once_a_month():
    oct1_before = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)   # 15:00 ET Thursday
    oct1_after = datetime(2026, 10, 1, 22, 30, tzinfo=UTC)   # 18:30 ET
    assert not L.due([], oct1_before) and L.due([], oct1_after)
    assert L.due([], datetime(2026, 10, 5, 12, 0, tzinfo=UTC))  # missed the 1st: made at the next run
    assert not L.due([{"type": "cohort", "month": "2026-10"}], oct1_after)
    assert not L.due([], datetime(2026, 11, 1, 23, 0, tzinfo=UTC))  # Sun 1 Nov: the first weekday is Mon 2 Nov
    assert L.due([], datetime(2026, 11, 2, 21, 30, tzinfo=UTC))
    assert not L.due([], datetime(2026, 9, 28, 22, 30, tzinfo=UTC))  # late September: wait for 1 October


def test_choose_by_rating_then_momentum():
    r = [{"ticker": "A", "rating": 4, "r12": 0.1}, {"ticker": "B", "rating": 5, "r12": -0.2},
         {"ticker": "C", "rating": 4, "r12": 0.3}, {"ticker": "D", "rating": 4, "r12": None}]
    assert [x["ticker"] for x in L.choose(r, 3)] == ["B", "C", "A"]


def test_score_after_63_days_net_of_costs():
    idx = pd.bdate_range("2026-10-01", periods=70)
    opens = pd.DataFrame({"AAA": 100.0, "BBB": 100.0, "SPY": 100.0}, index=idx)
    opens.iloc[1 + 63:, 0] = 110.0  # AAA +10% by the exit open
    opens.iloc[1 + 63:, 2] = 102.0  # SPY +2%
    recs = [{"type": "cohort", "month": "2026-10", "made_on": "2026-10-01", "tickers": ["AAA", "BBB"]}]
    (res,) = L.score(recs, opens)
    assert res["entry"] == "2026-10-02" and res["priced"] == 2
    assert abs(res["excess_net"] - (0.05 - 0.02 - 0.004)) < 1e-9
    assert L.score(recs + [{"type": "result", "month": "2026-10"}], opens) == []
    assert L.score(recs, opens.iloc[:60]) == []  # not matured
