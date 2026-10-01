from __future__ import annotations

import pandas as pd
import pytest

from app.sandbox.overnight import data_check, overnight_excess, overnight_legs


def test_overnight_legs_and_compounding() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    opens = pd.Series([101.0, 110.0, 108.0, 120.0], index=dates)
    closes = pd.Series([105.0, 108.0, 120.0, 122.0], index=dates)
    legs = overnight_legs(opens, closes)
    expected = pd.DataFrame(
        {"overnight": [110 / 105 - 1, 108 / 108 - 1, 120 / 120 - 1],
         "intraday": [108 / 110 - 1, 120 / 108 - 1, 122 / 120 - 1],
         "full": [108 / 105 - 1, 120 / 108 - 1, 122 / 120 - 1]},
        index=dates[1:],
    )
    pd.testing.assert_frame_equal(legs, expected)
    compounded = (1 + legs["overnight"]) * (1 + legs["intraday"]) - 1
    assert (compounded - legs["full"]).abs().max() <= 1e-12


def test_overnight_excess_cost_rf_and_forward_fill() -> None:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    legs = pd.DataFrame({"overnight": [0.01, 0.02, 0.03]}, index=dates)
    rf = pd.Series([0.001, 0.003], index=[dates[1], dates[2]])
    result = overnight_excess(legs, rf, cost=0.0005)
    assert result.name == "ret"
    assert result.iloc[0] == pytest.approx(0.01 - 0.001)
    assert result.iloc[1] == pytest.approx(0.02 - 0.001 - 0.001)
    assert result.iloc[2] == pytest.approx(0.03 - 0.001 - 0.003)


def _synthetic_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    minute_rows = []
    daily_rows = []
    prior_close = 100.0
    factor = 0.97
    for i, date in enumerate(dates):
        open_price = 101.0 + i
        close_price = 102.0 + i
        ny_date = date.strftime("%Y-%m-%d")
        minute_rows.extend([
            {"ts": pd.Timestamp(f"{ny_date} 09:30", tz="America/New_York"),
             "open": open_price, "close": open_price},
            {"ts": pd.Timestamp(f"{ny_date} 15:59", tz="America/New_York"),
             "open": close_price, "close": close_price},
        ])
        daily_rows.append({"Open": open_price * factor, "Close": close_price * factor})
        prior_close = close_price
    del prior_close
    return pd.DataFrame(daily_rows, index=dates), pd.DataFrame(minute_rows)


def test_data_check_ratios_and_corruption() -> None:
    daily, minute = _synthetic_data()
    check = data_check(daily, minute)
    assert check["share_ok"] == 1.0
    assert check["days"] == 2

    corrupted = daily.copy()
    corrupted.iloc[1, corrupted.columns.get_loc("Open")] *= 1.02
    check = data_check(corrupted, minute)
    assert check["share_ok"] < 1.0
    assert any(date == "2024-01-03" for date, _ in check["worst"])
