"""
Message-passing protocol for the specialist agent swarm.

Design constraints this module enforces:
  1. Agents never see each other's raw chain-of-thought. They exchange
     `AgentMessage` objects whose `content` is a *structured, typed payload*
     (a Pydantic model registered in `PAYLOAD_REGISTRY`), not free text.
  2. Every message is attributable: it carries the producing agent's id/version,
     a monotonic run-scoped sequence number, and a list of `EvidenceRef` ids
     that justify any factual assertions inside the payload.
  3. Messages are immutable once published (frozen models) so the bus can be
     replayed for reproducibility/audit without risk of mutation bugs.

The bus itself (`MessageBus`) is intentionally boring: an in-process pub/sub
backed by asyncio.Queue per topic in dev, swappable for Redis Streams / Kafka
in production via the same `publish`/`subscribe` interface.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class AgentRole(str, Enum):
    CIO = "chief_investment_officer"
    PORTFOLIO_MANAGER = "portfolio_manager"
    FUNDAMENTAL_ANALYST = "fundamental_analyst"
    FINANCIAL_STATEMENT_ANALYST = "financial_statement_analyst"
    ACCOUNTING_EXPERT = "accounting_expert"
    MACRO_ECONOMIST = "macro_economist"
    TECHNICAL_ANALYST = "technical_analyst"
    OPTIONS_STRATEGIST = "options_strategist"
    VOLATILITY_ANALYST = "volatility_analyst"
    NEWS_ANALYST = "news_analyst"
    SENTIMENT_ANALYST = "sentiment_analyst"
    INDUSTRY_EXPERT = "industry_expert"
    SUPPLY_CHAIN_ANALYST = "supply_chain_analyst"
    COMPETITION_ANALYST = "competition_analyst"
    VALUATION_EXPERT = "valuation_expert"
    QUANT_RESEARCHER = "quant_researcher"
    FACTOR_MODEL_RESEARCHER = "factor_model_researcher"
    RISK_MANAGER = "risk_manager"
    BULL_THESIS = "bull_thesis_agent"
    BEAR_THESIS = "bear_thesis_agent"
    COMPLIANCE = "compliance_agent"
    PLANNER = "planner"


class MessageType(str, Enum):
    ANALYSIS = "analysis"                # a specialist's structured findings
    THESIS = "thesis"                    # bull/bear thesis payload
    CHALLENGE = "challenge"              # cross-examination / rebuttal
    RISK_FLAG = "risk_flag"
    CONSENSUS = "consensus"
    CONFIDENCE = "confidence"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"


class AgentMessage(BaseModel):
    """The only unit of information that crosses an agent boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    seq: int  # monotonic within a run, assigned by the bus at publish time
    topic: str
    sender: AgentRole
    message_type: MessageType
    created_at: datetime = Field(default_factory=utcnow)

    # Structured payload only. `payload_schema` names the Pydantic model in
    # PAYLOAD_REGISTRY so consumers can validate/parse without guessing.
    payload_schema: str
    payload: dict[str, Any]

    # Every factual claim inside `payload` should trace to one of these.
    evidence_ids: list[str] = Field(default_factory=list)

    # Optional: which message(s) this one responds to / rebuts.
    in_reply_to: list[str] = Field(default_factory=list)

    agent_version: str = "unspecified"
    prompt_version: str = "unspecified"


PAYLOAD_REGISTRY: dict[str, type[BaseModel]] = {}


def register_payload(cls: type[BaseModel]) -> type[BaseModel]:
    PAYLOAD_REGISTRY[cls.__name__] = cls
    return cls


class MessageBus:
    """In-process pub/sub. Swap for Redis Streams by implementing the same
    interface (publish/subscribe/history) — consumers never construct topics
    directly, they go through this class, so the swap is transparent."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[AgentMessage]]] = {}
        self._history: dict[str, list[AgentMessage]] = {}
        self._seq_counters: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def publish(self, msg: AgentMessage) -> AgentMessage:
        async with self._lock:
            seq = self._seq_counters.get(msg.run_id, 0)
            self._seq_counters[msg.run_id] = seq + 1
        msg = msg.model_copy(update={"seq": seq})
        self._history.setdefault(msg.run_id, []).append(msg)
        for q in self._queues.get(msg.topic, []):
            await q.put(msg)
        return msg

    def subscribe(self, topic: str) -> asyncio.Queue[AgentMessage]:
        q: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._queues.setdefault(topic, []).append(q)
        return q

    def history(self, run_id: str) -> list[AgentMessage]:
        """Full ordered transcript of a run — the audit log / replay source."""
        return list(self._history.get(run_id, []))
