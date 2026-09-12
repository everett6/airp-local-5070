"""
Kicks off a research run for a ticker. In this skeleton the orchestrator is
constructed with mock connectors/agents wired via `dependencies.py`
(not shown in full — see docs/API_SPEC.md for the intended production wiring
with real connectors, a Postgres-backed MemoryStore, and a Neo4j client).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

router = APIRouter()

# run_id -> status, populated by the (stubbed) background job runner.
_RUN_STATUS: dict[str, str] = {}


class StartResearchRequest(BaseModel):
    ticker: str
    include_options: bool = True
    include_macro: bool = True


class StartResearchResponse(BaseModel):
    run_id: str
    status: str


@router.post("", response_model=StartResearchResponse)
async def start_research(req: StartResearchRequest, background_tasks: BackgroundTasks) -> StartResearchResponse:
    run_id = str(uuid.uuid4())
    _RUN_STATUS[run_id] = "queued"
    # background_tasks.add_task(run_research_pipeline, run_id, req.ticker)
    return StartResearchResponse(run_id=run_id, status="queued")


@router.get("/{run_id}/status")
async def get_status(run_id: str) -> dict:
    return {"run_id": run_id, "status": _RUN_STATUS.get(run_id, "unknown")}
