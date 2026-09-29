"""SPY overnight premium O1, specified in docs/PLAN_60_V2.md; see Cooper et al. (2008), Kelly and Clark (2011), and Boyarchenko et al. (NY Fed SR 917)."""
from __future__ import annotations

from typing import cast

import pandas as pd


def overnight_legs(opens: pd.Series, closes: pd.Series) -> pd.DataFrame:
    """Return overnight, intraday, and full-day returns from adjusted daily prices."""
    prices = pd.concat({"Open": opens, "Close": closes}, axis=1).dropna()
    previous_close = prices["Close"].shift(1)
    result = pd.DataFrame(index=prices.index)
    result["overnight"] = prices["Open"] / previous_close - 1
    result["intraday"] = prices["Close"] / prices["Open"] - 1
    result["full"] = prices["Close"] / previous_close - 1
    return result.iloc[1:]


def overnight_excess(legs: pd.DataFrame, rf_daily: pd.Series, cost: float = 1e-4) -> pd.Series:
    """Subtract two-sided transaction costs and forward-filled daily risk-free rate."""
    rf = rf_daily.reindex(legs.index, method="ffill").fillna(0.0)
    return (legs["overnight"] - 2 * cost - rf).rename("ret")


def data_check(daily: pd.DataFrame, minute: pd.DataFrame, tol: float = 0.005) -> dict[str, object]:
    """Compare Yahoo-adjusted daily ratios with 09:30/15:59 minute-bar ratios."""
    bars = minute.copy()
    bars["ts"] = pd.to_datetime(bars["ts"])
    local = bars["ts"].dt.tz_convert("America/New_York")
    bars["_date"] = local.dt.date
    bars["_time"] = local.dt.strftime("%H:%M")
    opens = bars.loc[bars["_time"] == "09:30"].set_index("_date")["open"]
    closes = bars.loc[bars["_time"] == "15:59"].set_index("_date")["close"]
    daily_by_date = daily.copy()
    daily_by_date.index = pd.to_datetime(daily_by_date.index).date
    dates = daily_by_date.index.intersection(opens.index).intersection(closes.index)
    dates = dates.sort_values()
    daily_dates = daily_by_date.index.sort_values()
    prior_dates = dict(zip(daily_dates[1:], daily_dates[:-1], strict=True))
    minute_closes = closes  # the 15:59 bar, never an after-hours print
    worst: list[tuple[str, float]] = []
    ok_count = 0
    compared = 0
    for date in dates:
        if date not in prior_dates:
            continue
        previous_date = prior_dates[date]
        if previous_date not in minute_closes.index:
            continue
        d_open = float(daily_by_date.loc[date, "Open"])
        d_close = float(daily_by_date.loc[date, "Close"])
        m_open = float(opens.loc[date])
        m_close = float(closes.loc[date])
        previous_daily_close = cast(float, daily_by_date.at[previous_date, "Close"])
        previous_minute_close = cast(float, minute_closes.at[previous_date])
        intraday_gap = abs((d_open / d_close) / (m_open / m_close) - 1)
        overnight_gap = abs((d_open / previous_daily_close) / (m_open / previous_minute_close) - 1)
        gap = max(intraday_gap, overnight_gap)
        compared += 1
        ok_count += int(intraday_gap <= tol and overnight_gap <= tol)
        worst.append((str(date), gap))
    worst.sort(key=lambda item: item[1], reverse=True)
    return {"days": compared, "share_ok": ok_count / compared if compared else 0.0,
            "worst": worst[:10]}
