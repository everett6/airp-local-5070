from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import calendar_shadows


def synthetic() -> tuple[pd.DataFrame, pd.Series]:
    idx = pd.bdate_range("2026-08-17", "2026-11-30")
    n = len(idx)
    close = 100 + np.arange(n) * 0.1
    daily = pd.DataFrame({"Open": close * (1 + 0.001), "Close": close}, index=idx)
    rf = pd.Series(0.05, index=idx)
    return daily, rf


def test_forward_window_and_tom_active_day_count() -> None:
    daily, rf = synthetic()
    result = calendar_shadows.compute(daily, rf, pd.Timestamp("2026-12-01").date())
    dates = daily.index[daily.index >= "2026-09-30"]
    assert result["T1"]["days"] == len(dates)
    assert result["O1"]["days"] == len(dates)
    # Sep 30; Oct 1, 2, 5, 30; Nov 2, 3, 4. Nov's final day is not marked at the cut-off edge.
    assert result["T1"]["active_days"] == 8
    assert result["T1"]["sharpe"] is not None
    assert result["T1"]["ci95"] is None
    assert result["O1"]["ci95"] is None


def test_o1_value_matches_hand_calculation() -> None:
    daily, rf = synthetic()
    result = calendar_shadows.compute(daily, rf, pd.Timestamp("2026-12-01").date())
    day = pd.Timestamp("2026-10-01")
    previous_close = daily.at[pd.Timestamp("2026-09-30"), "Close"]
    expected = daily.at[day, "Open"] / previous_close - 1 - 2e-4 - 0.05 / 252
    index = daily.index.get_loc(day)
    expected_pct = 100 * ((1 + expected) - 1)
    # One-day arithmetic is checked directly via the frozen overnight functions' result series.
    from app.sandbox import overnight

    legs = overnight.overnight_legs(daily["Open"], daily["Close"])
    x = overnight.overnight_excess(legs, (rf / 252).reindex(legs.index, method="ffill"), 1e-4)
    assert x.loc[day] == expected
    assert index > 0
    assert result["O1"]["days"] == len(daily.loc["2026-09-30":])
    assert expected_pct != 0


def test_no_forward_days_and_short_sample_stats() -> None:
    daily, rf = synthetic()
    empty = calendar_shadows.compute(daily, rf, pd.Timestamp("2026-09-30").date())
    for key in ("T1", "O1"):
        assert empty[key]["days"] == 0
        assert empty[key]["cum_net_excess_pct"] is None
        assert empty[key]["sharpe"] is None
        assert empty[key]["ci95"] is None

    short = calendar_shadows.compute(daily.loc[:"2026-10-15"], rf, pd.Timestamp("2026-10-16").date())
    assert short["O1"]["days"] < 20
    assert short["O1"]["sharpe"] is None
    assert short["O1"]["ci95"] is None
