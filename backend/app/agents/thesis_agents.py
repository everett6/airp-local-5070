"""
Bull and Bear thesis agents. They consume the *same* upstream specialist
findings (fundamental, macro, technical, options, news) but are prompted with
opposing mandates and are architecturally forbidden from seeing each other's
draft reasoning before publishing — they only see each other's *published*
theses on the bus, which is what makes the subsequent cross-examination
stage a genuine adversarial check rather than two agents converging early.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, SpecialistAgent
from app.core.message_protocol import AgentRole, register_payload
from app.evidence.models import Claim, ClaimBundle


class ThesisRequest(BaseModel):
    ticker: str
    specialist_findings: dict[str, dict]  # keyed by AgentRole.value -> finding dump


@register_payload
class Thesis(BaseModel):
    ticker: str
    stance: str  # "bull" | "bear"
    thesis_summary: str
    key_assumptions: list[str] = Field(
        description="Explicit assumptions this thesis depends on — required, "
        "not optional, per the Layer 5 debate spec."
    )
    key_uncertainties: list[str]
    missing_evidence: list[str] = Field(
        description="What data would most change this thesis if it existed but "
        "wasn't available/verified this run."
    )
    price_target: float
    price_target_source: str  # names which upstream numeric finding it derives from
    supporting_claim_ids: list[str]


class _ThesisAgentBase(SpecialistAgent[ThesisRequest, Thesis]):
    stance: str

    async def analyze(self, ctx: AgentContext, request: ThesisRequest) -> tuple[Thesis, ClaimBundle]:
        # `build_thesis_llm` is the ONLY tool this agent may call for synthesis;
        # it is an LLM call, but its output schema forces explicit assumptions/
        # uncertainties and forbids inventing new numbers (see prompt in
        # docs/AGENT_INTERFACES.md#thesis-agents) — any price target must cite
        # an existing numeric claim_id from specialist_findings.
        result = await self.call_tool(
            "build_thesis_llm",
            ticker=request.ticker,
            stance=self.stance,
            specialist_findings=request.specialist_findings,
        )

        thesis = Thesis(
            ticker=request.ticker,
            stance=self.stance,
            thesis_summary=result["summary"],
            key_assumptions=result["assumptions"],
            key_uncertainties=result["uncertainties"],
            missing_evidence=result["missing_evidence"],
            price_target=result["price_target"],
            price_target_source=result["price_target_source_claim_id"],
            supporting_claim_ids=result["supporting_claim_ids"],
        )

        claim = Claim(
            claim_id=f"{request.ticker}:{self.stance}_thesis",
            text=f"{self.stance.capitalize()} thesis for {request.ticker}: {result['summary'][:200]}",
            made_by=self.role.value,
            evidence_refs=[],  # the thesis itself cites *claim_ids*, not new evidence;
            is_numeric=False,  # verifier treats this as a synthesis claim, checked
        )                      # for internal consistency, not independent verification.
        return thesis, ClaimBundle(claims=[claim])


class BullThesisAgent(_ThesisAgentBase):
    role = AgentRole.BULL_THESIS
    stance = "bull"


class BearThesisAgent(_ThesisAgentBase):
    role = AgentRole.BEAR_THESIS
    stance = "bear"
