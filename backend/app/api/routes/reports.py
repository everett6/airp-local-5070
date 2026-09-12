from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/{run_id}")
async def get_report(run_id: str) -> dict:
    # Production: load the persisted ResearchReport row + rendered markdown
    # from Postgres (see db/schema.sql -> research_runs / reports tables).
    raise HTTPException(status_code=404, detail="report store not wired in this skeleton")


@router.get("/{run_id}/evidence")
async def get_report_evidence(run_id: str) -> dict:
    raise HTTPException(status_code=404, detail="evidence store not wired in this skeleton")
