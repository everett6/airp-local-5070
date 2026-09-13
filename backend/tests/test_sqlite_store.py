import asyncio
from datetime import UTC, datetime

import pytest

from app.sandbox.backtest import BacktestResult
from app.store.sqlite_store import BacktestHistoryStore


def make_result(ticker="ACME", run_id="r1", as_of=None) -> BacktestResult:
    return BacktestResult(
        run_id=run_id, ticker=ticker,
        as_of=as_of or datetime(2024, 3, 1, tzinfo=UTC),
        horizon_days=30, price_at_as_of=100.0, predicted_price=105.0,
        predicted_return_pct=5.0, actual_price=103.0, actual_return_pct=3.0,
        absolute_pct_error=2.0, directional_hit=True,
        self_check_passed=True, self_check_detail="Confirmed: blocked",
    )


@pytest.fixture
def store(tmp_path) -> BacktestHistoryStore:
    return BacktestHistoryStore(str(tmp_path / "test.db"))


def test_uses_in_memory_db_without_touching_filesystem():
    # ":memory:" must not attempt to create parent directories.
    store = BacktestHistoryStore(":memory:")
    assert store is not None


@pytest.mark.asyncio
async def test_record_and_list_recent_roundtrip(store):
    result = make_result()
    await store.record(result)

    rows = await store.list_recent()
    assert len(rows) == 1
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["ticker"] == "ACME"
    assert rows[0]["directional_hit"] == 1  # stored as int, sqlite has no native bool


@pytest.mark.asyncio
async def test_record_is_idempotent_on_same_run_id(store):
    result = make_result(run_id="dup")
    await store.record(result)
    await store.record(result)  # INSERT OR REPLACE — must not create a duplicate row

    rows = await store.list_recent()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_list_recent_filters_by_ticker(store):
    await store.record(make_result(ticker="ACME", run_id="a1"))
    await store.record(make_result(ticker="ZETA", run_id="z1"))

    acme_only = await store.list_recent(ticker="ACME")
    assert len(acme_only) == 1
    assert acme_only[0]["ticker"] == "ACME"


@pytest.mark.asyncio
async def test_list_recent_respects_limit(store):
    for i in range(5):
        await store.record(make_result(run_id=f"r{i}"))

    limited = await store.list_recent(limit=2)
    assert len(limited) == 2


@pytest.mark.asyncio
async def test_list_recent_orders_newest_first(store):
    # recorded_at defaults to sqlite's datetime('now'); insert with a tiny
    # delay so ordering is unambiguous rather than relying on same-tick ties.
    await store.record(make_result(run_id="first"))
    await asyncio.sleep(1.1)
    await store.record(make_result(run_id="second"))

    rows = await store.list_recent()
    assert rows[0]["run_id"] == "second"


@pytest.mark.asyncio
async def test_count_reflects_number_of_stored_results(store):
    assert await store.count() == 0
    await store.record(make_result(run_id="r1"))
    await store.record(make_result(run_id="r2"))
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_persists_across_store_instances_on_same_path(tmp_path):
    path = str(tmp_path / "persist.db")
    store1 = BacktestHistoryStore(path)
    await store1.record(make_result(run_id="persisted"))

    store2 = BacktestHistoryStore(path)  # simulates a process restart
    rows = await store2.list_recent()
    assert len(rows) == 1
    assert rows[0]["run_id"] == "persisted"


@pytest.mark.asyncio
async def test_concurrent_writes_do_not_corrupt_or_deadlock(store):
    results = [make_result(run_id=f"concurrent-{i}") for i in range(20)]
    await asyncio.gather(*(store.record(r) for r in results))

    assert await store.count() == 20
