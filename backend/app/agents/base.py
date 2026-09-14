"""
Base class every specialist agent implements.

Key isolation properties enforced by this interface (not just convention):
  - `tools` is an explicit allow-list; an agent can only call what's in its
    own dict, so a "Technical Analyst" cannot accidentally invoke the DCF
    engine, and a compliance review of an agent's blast radius is a one-line
    diff of this dict.
  - `memory_namespace` scopes long-term memory retrieval per agent (Layer 4
    requirement: "has its own memory"). Cross-agent memory reads go through
    the orchestrator's shared MemoryStore.query(), never direct namespace access.
  - `run()` takes and returns only typed Pydantic payloads registered in
    `PAYLOAD_REGISTRY` — no agent method signature accepts or returns `str`
    for its primary output, which is what forces claims to carry evidence
    (see evidence/models.py) instead of being embedded in prose.
"""
from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.core.message_protocol import AgentMessage, AgentRole, MessageBus, MessageType
from app.evidence.models import ClaimBundle
from app.memory.interface import MemoryQueryResult, MemoryStore

TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)

ToolFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    ticker: str
    bus: MessageBus
    memory: MemoryStore


class SpecialistAgent(abc.ABC, Generic[TIn, TOut]):
    role: AgentRole
    agent_version: str = "0.1.0"
    prompt_version: str = "0.1.0"

    def __init__(self, tools: dict[str, ToolFn], memory_namespace: str | None = None) -> None:
        self.tools = tools  # explicit allow-list; nothing outside this dict is reachable
        self.memory_namespace = memory_namespace or self.role.value

    @abc.abstractmethod
    async def analyze(self, ctx: AgentContext, request: TIn) -> tuple[TOut, ClaimBundle]:
        """Produce a structured output plus the claims (with evidence) backing it.
        Implementations MUST NOT compute financial numbers inline — any number in
        the output must come from a call into `self.tools` (the quant engine) or
        from a data connector, and must show up in the ClaimBundle with a
        numeric_source pointing at that tool's fully-qualified name.
        """
        raise NotImplementedError

    async def call_tool(self, name: str, /, **kwargs: Any) -> Any:
        if name not in self.tools:
            raise PermissionError(
                f"{self.role.value} attempted to call tool {name!r} which is not in "
                f"its allow-list: {sorted(self.tools)}"
            )
        return await self.tools[name](**kwargs)

    async def recall(self, ctx: AgentContext, query: str, tags: list[str] | None = None,
                     k: int = 5) -> list[MemoryQueryResult]:
        """Retrieval-only access to this agent's own memory namespace — enforces
        Layer 3's 'use retrieval instead of increasing context length'."""
        return await ctx.memory.query(
            namespace=self.memory_namespace, query=query, tags=tags or [], top_k=k
        )

    async def publish(
        self,
        ctx: AgentContext,
        message_type: MessageType,
        payload: BaseModel,
        evidence_ids: list[str],
        topic: str = "debate",
        in_reply_to: list[str] | None = None,
    ) -> AgentMessage:
        msg = AgentMessage(
            run_id=ctx.run_id,
            seq=-1,  # assigned by the bus
            topic=topic,
            sender=self.role,
            message_type=message_type,
            payload_schema=type(payload).__name__,
            payload=payload.model_dump(mode="json"),
            evidence_ids=evidence_ids,
            in_reply_to=in_reply_to or [],
            agent_version=self.agent_version,
            prompt_version=self.prompt_version,
        )
        return await ctx.bus.publish(msg)
