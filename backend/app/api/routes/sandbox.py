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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.sandbox.backtest import BacktestResult, run_backtest, run_backtest_suite

router = APIRouter()


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


def _today_utc() -> date:
    return datetime.now(UTC).date()


@router.post("/backtest", response_model=BacktestResultResponse)
async def backtest(req: BacktestRequest) -> BacktestResultResponse:
    if req.as_of >= _today_utc():
        raise HTTPException(
            status_code=400,
            detail="as_of must be in the past — the sandbox is for testing against "
                   "known history, not for pretending today is a backtest.",
        )
    as_of_dt = datetime(req.as_of.year, req.as_of.month, req.as_of.day, tzinfo=UTC)
    result = await run_backtest(req.ticker.upper(), as_of_dt, req.horizon_days)
    return BacktestResultResponse.from_result(result)


@router.post("/backtest-suite", response_model=list[BacktestResultResponse])
async def backtest_suite(req: BacktestSuiteRequest) -> list[BacktestResultResponse]:
    if req.end_date >= _today_utc():
        raise HTTPException(status_code=400, detail="end_date must be in the past.")
    if req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")

    results = await run_backtest_suite(
        req.ticker.upper(), req.start_date, req.end_date, req.horizon_days, req.step_days,
    )
    return [BacktestResultResponse.from_result(r) for r in results]
