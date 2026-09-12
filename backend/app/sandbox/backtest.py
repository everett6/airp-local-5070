"""
The backtest harness: given a ticker and a historical `as_of` date, produces
a prediction using ONLY information available as of that date, then —
separately, after leaving the sandbox — checks what actually happened and
grades the prediction. This is "a testing way" to find out whether the
system's predictions are worth anything, as opposed to a live run where you
won't know if it was right for months.

Two things make this trustworthy rather than just a UI toggle around a
regular run:

1. Every connector call made *while generating the prediction* goes through
   `sandbox.clock.enforce_point_in_time`. If anything in the prediction path
   ever tries to read data timestamped after `as_of`, the run raises
   `LookaheadViolation` instead of quietly succeeding — see
   `test_backtest.py::test_prediction_step_cannot_see_future_price` for a
   proof of this, not just an assertion.
2. The harness *itself* performs an explicit self-check on every run:
   immediately after generating the prediction (still inside the sandbox
   scope), it deliberately attempts to read the future outcome the same way
   a buggy connector might — and asserts that attempt fails. If it doesn't
   fail, `self_check_passed=False` on the result, which the UI surfaces
   prominently rather than hiding a broken guarantee behind a "looks fine"
   report.

The prediction itself is deliberately simple and fully deterministic
(quant, not an LLM) for this MVP: a short-lookback momentum extrapolation
computed via `quant.risk`-style primitives. This is intentional — the point
of this module is to prove the *harness* is airtight before layering richer
(LLM-assisted) forecasting on top of it. An LLM-based thesis can be added as
an additional prediction strategy later (see docs/ROADMAP.md) as long as it
goes through the same sandboxed connectors.
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.data_ingestion.base_connector import InMemoryCache
from app.data_ingestion.market_data import MockMarketDataConnector
from app.sandbox.clock import LookaheadViolation, sandbox_scope
from app.sandbox.synthetic_market import deterministic_price, deterministic_return_series


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    ticker: str
    as_of: datetime
    horizon_days: int
    price_at_as_of: float
    predicted_price: float
    predicted_return_pct: float
    actual_price: float
    actual_return_pct: float
    absolute_pct_error: float
    directional_hit: bool
    self_check_passed: bool
    self_check_detail: str


def _midnight_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def _momentum_prediction(ticker: str, as_of: datetime, horizon_days: int, lookback_days: int = 20) -> float:
    """The only "model" in this MVP: mean daily return over the lookback
    window, compounded forward `horizon_days`. Computed entirely from data
    at or before `as_of` — never from `datetime.now()`."""
    connector = MockMarketDataConnector(cache=InMemoryCache())
    quote_record = await connector.fetch(cache_key=f"bt:{ticker}:{as_of.isoformat()}", ticker=ticker, as_of=as_of)
    current_price = quote_record.data.price

    returns = deterministic_return_series(ticker, as_of, n_days=lookback_days)
    mean_daily_return = statistics.fmean(returns) if returns else 0.0

    predicted_price = current_price * ((1 + mean_daily_return) ** horizon_days)
    return round(predicted_price, 2)


async def run_backtest(ticker: str, as_of: datetime, horizon_days: int = 30) -> BacktestResult:
    run_id = str(uuid.uuid4())
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    self_check_passed = False
    self_check_detail = ""

    with sandbox_scope(as_of=as_of, run_id=run_id):
        connector = MockMarketDataConnector(cache=InMemoryCache())
        current_quote = await connector.fetch(
            cache_key=f"bt:current:{ticker}:{as_of.isoformat()}", ticker=ticker, as_of=as_of
        )
        price_at_as_of = current_quote.data.price

        predicted_price = await _momentum_prediction(ticker, as_of, horizon_days)

        # Self-check: while still inside the sandbox, deliberately try to read
        # the future outcome the way a buggy connector/agent might. This MUST
        # raise — if it doesn't, the harness itself is broken and the result
        # says so explicitly rather than producing a falsely-clean report.
        future_date = as_of + timedelta(days=horizon_days)
        try:
            await connector.fetch(
                cache_key=f"bt:future-selfcheck:{ticker}:{future_date.isoformat()}",
                ticker=ticker, as_of=future_date,
            )
            self_check_detail = (
                "CRITICAL: fetching future data inside the sandbox scope did NOT raise "
                "LookaheadViolation. The sandbox guarantee is broken — do not trust this result."
            )
        except LookaheadViolation as exc:
            self_check_passed = True
            self_check_detail = f"Confirmed: future data access correctly blocked ({exc})"

    # Outside the sandbox now — safe to look up what "actually" happened,
    # exactly like waiting for time to pass in a real backtest.
    future_date = as_of + timedelta(days=horizon_days)
    actual_price = deterministic_price(ticker, future_date)

    predicted_return_pct = (predicted_price - price_at_as_of) / price_at_as_of if price_at_as_of else 0.0
    actual_return_pct = (actual_price - price_at_as_of) / price_at_as_of if price_at_as_of else 0.0
    absolute_pct_error = abs(actual_price - predicted_price) / price_at_as_of if price_at_as_of else 0.0
    directional_hit = (predicted_return_pct >= 0) == (actual_return_pct >= 0)

    return BacktestResult(
        run_id=run_id, ticker=ticker, as_of=as_of, horizon_days=horizon_days,
        price_at_as_of=price_at_as_of, predicted_price=predicted_price,
        predicted_return_pct=round(predicted_return_pct * 100, 3),
        actual_price=actual_price, actual_return_pct=round(actual_return_pct * 100, 3),
        absolute_pct_error=round(absolute_pct_error * 100, 3),
        directional_hit=directional_hit,
        self_check_passed=self_check_passed, self_check_detail=self_check_detail,
    )


async def run_backtest_suite(
    ticker: str, start_date: date, end_date: date, horizon_days: int, step_days: int = 30
) -> list[BacktestResult]:
    """Runs many backtests across a historical window, e.g. one per month
    over the last two years, so accuracy can be judged from a distribution
    rather than a single cherry-pickable date. This is what the UI's
    "Sandbox" page drives for a proper evaluation instead of one-off runs."""
    results = []
    current = start_date
    while current <= end_date:
        result = await run_backtest(ticker, _midnight_utc(current), horizon_days)
        results.append(result)
        current += timedelta(days=step_days)
    return results
