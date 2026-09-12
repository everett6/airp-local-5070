"""
A deterministic, closed-form "price path" that is a pure function of
(ticker, calendar date) — never of `datetime.now()`, never of any random
seed drawn at call time. This is what makes backtesting with the mock
connectors *honest*: the same (ticker, date) always produces the same price,
whether you ask for it today or in five years, and asking for date D+30
requires no information from date D that wasn't already implied by the
formula — there's no hidden state a lookahead bug could leak through.

This is explicitly NOT a market simulator and makes no claim to resemble
real price dynamics. It exists to exercise the sandbox/lookahead-prevention
*mechanism* end-to-end without needing a licensed historical data provider.
Swapping in a real point-in-time market data vendor (see docs/ROADMAP.md) is
what turns this from "the plumbing works" into "the predictions mean
something" — do not use this for actual investment decisions.
"""
from __future__ import annotations

import math
from datetime import date, datetime


def deterministic_price(ticker: str, as_of: datetime | date) -> float:
    if isinstance(as_of, datetime):
        as_of = as_of.date()

    day_index = as_of.toordinal()
    ticker_seed = sum(ord(c) for c in ticker.upper())

    base = 100.0 + (ticker_seed % 400)
    slow_wave = 15.0 * math.sin(day_index * 0.013 + ticker_seed)
    fast_wave = 6.0 * math.sin(day_index * 0.071 + ticker_seed * 1.7)
    drift = 0.01 * (day_index % 365 - 180) * (ticker_seed % 5 - 2) / 100.0

    price = base + slow_wave + fast_wave + base * drift
    return round(max(1.0, price), 2)


def deterministic_daily_return(ticker: str, as_of: datetime | date) -> float:
    """Same-formula 1-day return, for building a synthetic return series a
    Risk Manager agent can run VaR/Sharpe/drawdown against without any real
    market data license."""
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    today_price = deterministic_price(ticker, as_of)
    yesterday = date.fromordinal(as_of.toordinal() - 1)
    yesterday_price = deterministic_price(ticker, yesterday)
    if yesterday_price == 0:
        return 0.0
    return (today_price - yesterday_price) / yesterday_price


def deterministic_return_series(ticker: str, as_of: datetime | date, n_days: int) -> list[float]:
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    return [
        deterministic_daily_return(ticker, date.fromordinal(as_of.toordinal() - i))
        for i in range(n_days, 0, -1)
    ]
