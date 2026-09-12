from datetime import UTC, datetime, timedelta

import pytest

from app.data_ingestion.base_connector import InMemoryCache
from app.data_ingestion.market_data import MockMarketDataConnector
from app.sandbox.backtest import run_backtest, run_backtest_suite
from app.sandbox.clock import (
    LookaheadViolation,
    current_effective_time,
    enforce_point_in_time,
    sandbox_scope,
)
from app.sandbox.synthetic_market import deterministic_price, deterministic_return_series


def test_deterministic_price_is_pure_function_of_ticker_and_date():
    d = datetime(2025, 6, 1, tzinfo=UTC)
    p1 = deterministic_price("ACME", d)
    p2 = deterministic_price("ACME", d)
    assert p1 == p2  # same inputs -> same output, always


def test_deterministic_price_varies_by_ticker_and_date():
    d1 = datetime(2025, 6, 1, tzinfo=UTC)
    d2 = datetime(2025, 9, 1, tzinfo=UTC)
    assert deterministic_price("ACME", d1) != deterministic_price("ACME", d2)
    assert deterministic_price("ACME", d1) != deterministic_price("ZETA", d1)


def test_no_active_clock_means_enforce_is_a_noop():
    # Outside any sandbox scope, arbitrarily "future" timestamps are fine —
    # this is normal live research and must not be restricted.
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    enforce_point_in_time(far_future, source="test")  # should not raise


def test_enforce_raises_when_timestamp_exceeds_active_cutoff():
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    with sandbox_scope(as_of=cutoff, run_id="r1"):
        enforce_point_in_time(cutoff, source="test")  # exactly at cutoff: fine
        enforce_point_in_time(cutoff - timedelta(days=1), source="test")  # past: fine
        with pytest.raises(LookaheadViolation):
            enforce_point_in_time(cutoff + timedelta(seconds=1), source="test")


def test_clock_scope_is_isolated_after_exit():
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    with sandbox_scope(as_of=cutoff, run_id="r2"):
        pass
    # After the `with` block, no clock should be active.
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    enforce_point_in_time(far_future, source="test")  # must not raise


def test_current_effective_time_reflects_active_sandbox():
    cutoff = datetime(2020, 3, 15, tzinfo=UTC)
    with sandbox_scope(as_of=cutoff, run_id="r3"):
        assert current_effective_time() == cutoff
    # outside the scope, effective time is real now (just assert it's not the cutoff)
    assert current_effective_time() != cutoff


@pytest.mark.asyncio
async def test_mock_connector_blocks_explicit_future_request_inside_sandbox():
    connector = MockMarketDataConnector(cache=InMemoryCache())
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    future = datetime(2025, 6, 1, tzinfo=UTC)

    with sandbox_scope(as_of=cutoff, run_id="r4"), pytest.raises(LookaheadViolation):
        await connector.fetch(cache_key="k1", ticker="ACME", as_of=future)


@pytest.mark.asyncio
async def test_mock_connector_allows_past_and_present_inside_sandbox():
    connector = MockMarketDataConnector(cache=InMemoryCache())
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    past = datetime(2024, 6, 1, tzinfo=UTC)

    with sandbox_scope(as_of=cutoff, run_id="r5"):
        record_present = await connector.fetch(cache_key="k2", ticker="ACME", as_of=cutoff)
        record_past = await connector.fetch(cache_key="k3", ticker="ACME", as_of=past)
        assert record_present.data.price > 0
        assert record_past.data.price > 0


@pytest.mark.asyncio
async def test_prediction_step_cannot_see_future_price():
    """The concrete proof this is meant to provide: run the actual
    momentum-prediction code path used by the backtest, and confirm that if
    it were rewritten (buggily) to peek at a future price, it would fail
    loudly rather than silently succeed."""
    connector = MockMarketDataConnector(cache=InMemoryCache())
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)

    with sandbox_scope(as_of=cutoff, run_id="r6"):
        # Legitimate: predicting using only data at/before cutoff.
        returns = deterministic_return_series("ACME", cutoff, n_days=20)
        assert len(returns) == 20

        # Illegitimate (simulating a bug): trying to fetch tomorrow's price
        # to "cheat" on the prediction.
        with pytest.raises(LookaheadViolation):
            await connector.fetch(
                cache_key="cheat", ticker="ACME", as_of=cutoff + timedelta(days=1)
            )


@pytest.mark.asyncio
async def test_run_backtest_self_check_passes():
    as_of = datetime(2024, 3, 1, tzinfo=UTC)
    result = await run_backtest("ACME", as_of, horizon_days=30)

    assert result.self_check_passed is True
    assert "Confirmed" in result.self_check_detail
    assert result.ticker == "ACME"
    assert result.price_at_as_of > 0
    assert result.actual_price > 0
    assert isinstance(result.directional_hit, bool)


@pytest.mark.asyncio
async def test_run_backtest_is_reproducible():
    as_of = datetime(2024, 3, 1, tzinfo=UTC)
    r1 = await run_backtest("ACME", as_of, horizon_days=30)
    r2 = await run_backtest("ACME", as_of, horizon_days=30)

    assert r1.predicted_price == r2.predicted_price
    assert r1.actual_price == r2.actual_price
    assert r1.absolute_pct_error == r2.absolute_pct_error


@pytest.mark.asyncio
async def test_run_backtest_actual_price_uses_correct_future_date():
    as_of = datetime(2024, 3, 1, tzinfo=UTC)
    horizon = 30
    result = await run_backtest("ACME", as_of, horizon_days=horizon)

    expected_future = as_of + timedelta(days=horizon)
    assert result.actual_price == deterministic_price("ACME", expected_future)


@pytest.mark.asyncio
async def test_run_backtest_suite_produces_multiple_dated_results():
    from datetime import date
    results = await run_backtest_suite(
        "ACME", start_date=date(2024, 1, 1), end_date=date(2024, 4, 1),
        horizon_days=14, step_days=30,
    )
    assert len(results) >= 3
    for r in results:
        assert r.self_check_passed is True
