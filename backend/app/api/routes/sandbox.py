"""
The sandbox/backtest endpoints — the "testing way" surfaced to the UI.

Every response includes `self_check_passed`. The frontend is expected to
treat `self_check_passed=False` as a hard-stop warning, not a minor caveat:
it means the lookahead-prevention guarantee failed on that specific run, so
the result should not be trusted. See app/sandbox/backtest.py's module
docstring for why this self-check exists on every single run rather than
being a one-time test suite assertion.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.sandbox.backtest import BacktestResult, run_backtest, run_backtest_suite
from app.store.sqlite_store import BacktestHistoryStore

router = APIRouter()

# One process-lifetime store instance by default. Cheap to construct (just
# ensures the schema exists) and sqlite3 handles its own file-level locking,
# so a single shared instance across requests is the right call for this
# workload (see sqlite_store.py's module docstring). Exposed as a FastAPI
# dependency (not called directly in route bodies) specifically so tests can
# override it with a temp-file-backed store via app.dependency_overrides
# instead of writing into the real dev database.
_history_store: BacktestHistoryStore | None = None


def get_history_store() -> BacktestHistoryStore:
    global _history_store
    if _history_store is None:
        _history_store = BacktestHistoryStore(get_settings().sqlite_path)
    return _history_store


class BacktestRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    as_of: date = Field(description="The historical date to pretend 'today' is.")
    horizon_days: int = Field(default=30, ge=1, le=365)


class BacktestSuiteRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    start_date: date
    end_date: date
    horizon_days: int = Field(default=30, ge=1, le=365)
    step_days: int = Field(default=30, ge=1, le=180)


class BacktestResultResponse(BaseModel):
    run_id: str
    ticker: str
    as_of: str
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

    @classmethod
    def from_result(cls, r: BacktestResult) -> BacktestResultResponse:
        return cls(
            run_id=r.run_id, ticker=r.ticker, as_of=r.as_of.isoformat(),
            horizon_days=r.horizon_days, price_at_as_of=r.price_at_as_of,
            predicted_price=r.predicted_price, predicted_return_pct=r.predicted_return_pct,
            actual_price=r.actual_price, actual_return_pct=r.actual_return_pct,
            absolute_pct_error=r.absolute_pct_error, directional_hit=r.directional_hit,
            self_check_passed=r.self_check_passed, self_check_detail=r.self_check_detail,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BacktestResultResponse:
        """Same shape, built from a SQLite row dict (history endpoint) instead
        of a fresh BacktestResult — kept as a separate constructor rather than
        overloading from_result so neither caller has to fake fields it
        doesn't have."""
        return cls(
            run_id=row["run_id"], ticker=row["ticker"], as_of=row["as_of"],
            horizon_days=row["horizon_days"], price_at_as_of=row["price_at_as_of"],
            predicted_price=row["predicted_price"], predicted_return_pct=row["predicted_return_pct"],
            actual_price=row["actual_price"], actual_return_pct=row["actual_return_pct"],
            absolute_pct_error=row["absolute_pct_error"], directional_hit=bool(row["directional_hit"]),
            self_check_passed=bool(row["self_check_passed"]), self_check_detail=row["self_check_detail"],
        )


def _today_utc() -> date:
    return datetime.now(UTC).date()


@router.post("/backtest", response_model=BacktestResultResponse)
async def backtest(
    req: BacktestRequest, store: BacktestHistoryStore = Depends(get_history_store)
) -> BacktestResultResponse:
    if req.as_of >= _today_utc():
        raise HTTPException(
            status_code=400,
            detail="as_of must be in the past — the sandbox is for testing against "
                   "known history, not for pretending today is a backtest.",
        )
    as_of_dt = datetime(req.as_of.year, req.as_of.month, req.as_of.day, tzinfo=UTC)
    result = await run_backtest(req.ticker.upper(), as_of_dt, req.horizon_days)
    await store.record(result)
    return BacktestResultResponse.from_result(result)


@router.post("/backtest-suite", response_model=list[BacktestResultResponse])
async def backtest_suite(
    req: BacktestSuiteRequest, store: BacktestHistoryStore = Depends(get_history_store)
) -> list[BacktestResultResponse]:
    if req.end_date >= _today_utc():
        raise HTTPException(status_code=400, detail="end_date must be in the past.")
    if req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")

    results = await run_backtest_suite(
        req.ticker.upper(), req.start_date, req.end_date, req.horizon_days, req.step_days,
    )
    for result in results:
        await store.record(result)
    return [BacktestResultResponse.from_result(r) for r in results]


@router.get("/history", response_model=list[BacktestResultResponse])
async def history(
    ticker: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=20, ge=1, le=200),
    store: BacktestHistoryStore = Depends(get_history_store),
) -> list[BacktestResultResponse]:
    rows = await store.list_recent(ticker.upper() if ticker else None, limit)
    return [BacktestResultResponse.from_row(r) for r in rows]
