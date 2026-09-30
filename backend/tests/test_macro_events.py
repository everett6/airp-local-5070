import pandas as pd

from app.sandbox.macro_events import announcement_strategy, event_days
from scripts.macro_calendar import count_exceptions


def test_event_days_marks_only_requested_trading_dates():
    index = pd.bdate_range("2024-01-01", periods=5)
    dates = [index[1].strftime("%Y-%m-%d"), index[3].strftime("%Y-%m-%d"), "2024-02-01"]
    result = event_days(index, dates)
    assert result.tolist() == [False, True, False, True, False]


def test_announcement_strategy_costs_first_and_last_day_of_each_run():
    index = pd.bdate_range("2024-01-01", periods=10)
    excess = pd.Series([0.01] * 10, index=index)
    events = pd.Series([False, True, False, False, True, True, False, True, False, False], index=index)
    result = announcement_strategy(excess, events, cost=0.001)
    expected = [0.0, 0.008, 0.0, 0.0, 0.009, 0.009, 0.0, 0.008, 0.0, 0.0]
    assert result["held"].tolist() == events.tolist()
    assert all(abs(actual - target) < 1e-12 for actual, target in zip(result["ret"], expected))


def test_count_checker_flags_unexplained_and_accepts_noted_exception():
    rows = []
    for day in pd.date_range("2019-01-01", periods=10, freq="MS"):
        rows.append({"date": day.strftime("%Y-%m-%d"), "event": "jobs", "note": ""})
    for event, periods in (("ppi", 12), ("fomc", 8)):
        for day in pd.date_range("2019-01-01", periods=periods, freq="MS"):
            rows.append({"date": day.strftime("%Y-%m-%d"), "event": event, "note": ""})
    frame = pd.DataFrame(rows)
    assert count_exceptions(frame, 2019, 2019) == ["2019 jobs count 10 without note"]
    frame.loc[0, "note"] = "2019 exceptional delayed/missing release documented"
    assert count_exceptions(frame, 2019, 2019) == []
