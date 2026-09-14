"""
Orchestrates the Layer 5 debate flow across the specialist swarm. This class
owns *sequencing and state*, not reasoning — every substantive judgment is
delegated to an agent or a deterministic function. That separation is what
lets us unit test the flow (test_debate_orchestrator.py) without any LLM.

Fault tolerance: each stage is wrapped so that a single agent failure degrades
that stage's contribution (recorded as a `StageError`) rather than aborting
the whole run. A run can still produce a report with explicitly reduced
confidence and a visible gap, which is preferable to either a crash or a
silently incomplete-but-confident report.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentContext
from app.core.message_protocol import AgentRole, MessageBus
from app.debate.stages import DebateStage, compute_agreement
from app.evidence.models import ClaimBundle

logger = logging.getLogger("airp.debate")


@dataclass
class StageError:
    stage: DebateStage
    agent: AgentRole
    error: str


@dataclass
class DebateRunState:
    run_id: str
    ticker: str
    stage: DebateStage = DebateStage.PLANNING
    findings: dict[str, Any] = field(default_factory=dict)     # AgentRole.value -> output dump
    claims: list[ClaimBundle] = field(default_factory=list)
    errors: list[StageError] = field(default_factory=list)
    unresolved_challenges: int = 0
    bull_price_target: float | None = None
    bear_price_target: float | None = None
    current_price: float | None = None


class DebateOrchestrator:
    def __init__(self, bus: MessageBus, agents: dict[AgentRole, Any]) -> None:
        self._bus = bus
        self._agents = agents  # role -> SpecialistAgent instance

    async def run(
        self, ctx: AgentContext, independent_roles: list[AgentRole], current_price: float | None = None
    ) -> DebateRunState:
        # `current_price` anchors the consensus stage's agreement score (see
        # DebateStage.CONSENSUS below). It's threaded in explicitly rather than
        # fetched implicitly here so callers control exactly which quote/version
        # of the price was used — reproducibility over convenience.
        state = DebateRunState(run_id=ctx.run_id, ticker=ctx.ticker, current_price=current_price)

        state.stage = DebateStage.INDEPENDENT_ANALYSIS
        for role in independent_roles:
            await self._run_agent_stage(ctx, state, role, request_builder=self._build_specialist_request)

        state.stage = DebateStage.BULL_THESIS
        await self._run_agent_stage(ctx, state, AgentRole.BULL_THESIS, request_builder=self._build_thesis_request)

        state.stage = DebateStage.BEAR_THESIS
        await self._run_agent_stage(ctx, state, AgentRole.BEAR_THESIS, request_builder=self._build_thesis_request)

        state.stage = DebateStage.CROSS_EXAMINATION
        state.unresolved_challenges = await self._cross_examine(ctx, state)

        state.stage = DebateStage.MACRO_CHALLENGE
        await self._run_agent_stage(ctx, state, AgentRole.MACRO_ECONOMIST, request_builder=self._build_specialist_request)

        state.stage = DebateStage.RISK_CHALLENGE
        await self._run_agent_stage(ctx, state, AgentRole.RISK_MANAGER, request_builder=self._build_specialist_request)

        state.stage = DebateStage.PORTFOLIO_REVIEW
        await self._run_agent_stage(ctx, state, AgentRole.PORTFOLIO_MANAGER, request_builder=self._build_specialist_request)

        state.stage = DebateStage.CONSENSUS
        # Consensus is a deterministic aggregation, not a re-ask of an LLM to
        # "decide who's right" — see compute_agreement.
        if state.bull_price_target is not None and state.bear_price_target is not None and state.current_price:
            state.findings["agreement_score"] = compute_agreement(
                state.bull_price_target, state.bear_price_target, state.current_price
            )

        state.stage = DebateStage.DONE
        return state

    async def _run_agent_stage(self, ctx: AgentContext, state: DebateRunState, role: AgentRole,
                               request_builder: Callable[[AgentRole, DebateRunState], BaseModel]) -> None:
        agent = self._agents.get(role)
        if agent is None:
            state.errors.append(StageError(stage=state.stage, agent=role, error="agent not registered"))
            return
        try:
            request = request_builder(role, state)
            output, claims = await agent.analyze(ctx, request)
            state.findings[role.value] = output.model_dump(mode="json")
            state.claims.append(claims)
            if role == AgentRole.BULL_THESIS:
                state.bull_price_target = output.price_target
            if role == AgentRole.BEAR_THESIS:
                state.bear_price_target = output.price_target
        except Exception as exc:
            logger.exception("stage %s failed for agent %s", state.stage, role)
            state.errors.append(StageError(stage=state.stage, agent=role, error=str(exc)))

    def _build_specialist_request(self, role: AgentRole, state: DebateRunState) -> BaseModel:
        from app.agents.fundamental_analyst import FundamentalAnalysisRequest
        # In production this dispatches to a per-role request-model factory
        # registered alongside each agent; kept simple here for the skeleton.
        return FundamentalAnalysisRequest(ticker=state.ticker, fiscal_period="latest")

    def _build_thesis_request(self, role: AgentRole, state: DebateRunState) -> BaseModel:
        from app.agents.thesis_agents import ThesisRequest
        return ThesisRequest(ticker=state.ticker, specialist_findings=dict(state.findings))

    async def _cross_examine(self, ctx: AgentContext, state: DebateRunState) -> int:
        """Deterministic rule-based challenge detector: flags theses whose
        stated assumptions contradict a specialist finding already on record.
        This is intentionally simple and auditable rather than another LLM
        call — the *goal* of this stage is to catch things a purely generative
        cross-exam would blur past, e.g. a bull thesis assuming margin
        expansion while the fundamental analyst's own finding shows margin
        compression."""
        unresolved = 0
        bull = state.findings.get(AgentRole.BULL_THESIS.value)
        bear = state.findings.get(AgentRole.BEAR_THESIS.value)
        fundamentals = state.findings.get(AgentRole.FUNDAMENTAL_ANALYST.value)

        if bull and fundamentals and \
           "margin expansion" in " ".join(bull.get("key_assumptions", [])).lower() and \
           fundamentals.get("operating_margin", 1.0) < 0:
            unresolved += 1
        if bear and fundamentals and \
           "runway" in " ".join(bear.get("key_assumptions", [])).lower() and \
           fundamentals.get("free_cash_flow", -1.0) > 0:
            unresolved += 1
        return unresolved
