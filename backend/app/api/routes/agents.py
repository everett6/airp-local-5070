from fastapi import APIRouter

from app.core.message_protocol import AgentRole

router = APIRouter()


@router.get("")
async def list_agents() -> dict:
    return {"agents": [role.value for role in AgentRole]}
