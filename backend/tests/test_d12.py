from datetime import date

import pandas as pd

from app.sandbox.intraday import d12_ai_earnings


def frames():
    idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    opens = pd.DataFrame({"AAA": [10.0, 10.0, 10.0], "BBB": [20.0, 20.0, 20.0], "SPY": [100.0, 100.0, 100.0]}, idx)
    closes = pd.DataFrame({"AAA": [11.0, 10.0, 9.0], "BBB": [19.0, 20.0, 22.0], "SPY": [101.0, 100.0, 100.0]}, idx)
    return opens, closes


def rel(rows):
    df = pd.DataFrame(rows, columns=["ticker", "accepted_utc", "logodds"])
    df["accepted_utc"] = pd.to_datetime(df["accepted_utc"], utc=True)
    return df


def entry(ts):
    return {date(2025, 1, 1): date(2025, 1, 2), date(2025, 1, 2): date(2025, 1, 3)}.get(ts.date(), date(2025, 1, 6))


def test_side_from_the_median_of_strictly_earlier_scores_and_hedged_return():
    opens, closes = frames()
    r = rel([("AAA", "2025-01-01T10:00", 0.0), ("BBB", "2025-01-01T11:00", 2.0),
             ("AAA", "2025-01-01T12:00", 5.0),        # above median(0, 2) = 1 -> long on 2025-01-02
             ("BBB", "2025-01-05T12:00", -1.0)])      # below median(2, 5) = 3.5 -> short on 2025-01-06
    daily, trades, counts = d12_ai_earnings(r, opens, closes, entry, cost=0.001, warmup=2)
    assert counts == {"releases": 4, "warmup": 2, "no_prices": 0}
    assert list(trades["side"]) == [1, -1]
    assert trades["ret"].round(6).tolist() == [round(0.10 - 0.01 - 0.001, 6), round(-(0.10 - 0.0) - 0.001, 6)]
    assert daily.index.min() == pd.Timestamp("2025-01-02") and daily.loc["2025-01-03"] == 0.0


def test_ties_do_not_see_each_other_and_missing_prices_are_counted():
    opens, closes = frames()
    r = rel([("AAA", "2025-01-01T10:00", 1.0), ("BBB", "2025-01-01T10:00", 3.0), ("ZZZ", "2025-01-01T12:00", 9.0)])
    daily, trades, counts = d12_ai_earnings(r, opens, closes, entry, cost=0.0, warmup=2)
    assert counts["warmup"] == 2 and counts["no_prices"] == 1 and trades.empty and daily.empty
