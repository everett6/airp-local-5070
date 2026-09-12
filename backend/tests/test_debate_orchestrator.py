import pytest
from pydantic import BaseModel

from app.agents.base import AgentContext, SpecialistAgent
from app.core.message_protocol import AgentRole, MessageBus
from app.debate.orchestrator import DebateOrchestrator
from app.debate.stages import DebateStage, compute_agreement
from app.evidence.models import Claim, ClaimBundle
from app.memory.postgres_store import InMemoryStore


class DummyRequest(BaseModel):
    ticker: str
    fiscal_period: str = "latest"


class DummyFinding(BaseModel):
    ticker: str
    operating_margin: float = 0.2
    free_cash_flow: float = 500.0
    narrative_summary: str = "dummy finding"


class DummyThesis(BaseModel):
    ticker: str
    stance: str
    thesis_summary: str
    key_assumptions: list[str]
    key_uncertainties: list[str]
    missing_evidence: list[str]
    price_target: float
    price_target_source: str
    supporting_claim_ids: list[str]


class DummySpecialist(SpecialistAgent[DummyRequest, DummyFinding]):
    def __init__(self, role):
        self.role = role
        super().__init__(tools={})

    async def analyze(self, ctx, request):
        finding = DummyFinding(ticker=request.ticker)
        claim = Claim(claim_id=f"{request.ticker}:{self.role.value}", text="dummy",
                       made_by=self.role.value, is_numeric=False)
        return finding, ClaimBundle(claims=[claim])


class DummyThesisAgent(SpecialistAgent[DummyRequest, DummyThesis]):
    def __init__(self, role, stance, price_target):
        self.role = role
        super().__init__(tools={})
        self.stance = stance
        self.price_target = price_target

    async def analyze(self, ctx, request):
        thesis = DummyThesis(
            ticker=request.ticker, stance=self.stance, thesis_summary=f"{self.stance} case",
            key_assumptions=["margin stable"], key_uncertainties=["macro"],
            missing_evidence=[], price_target=self.price_target,
            price_target_source="dummy_claim", supporting_claim_ids=[],
        )
        claim = Claim(claim_id=f"{request.ticker}:{self.stance}", text="dummy thesis",
                       made_by=self.role.value, is_numeric=False)
        return thesis, ClaimBundle(claims=[claim])


class FailingAgent(SpecialistAgent[DummyRequest, DummyFinding]):
    def __init__(self, role):
        self.role = role
        super().__init__(tools={})

    async def analyze(self, ctx, request):
        raise RuntimeError("simulated upstream data failure")


@pytest.mark.asyncio
async def test_debate_run_happy_path():
    bus = MessageBus()
    agents = {
        AgentRole.FUNDAMENTAL_ANALYST: DummySpecialist(AgentRole.FUNDAMENTAL_ANALYST),
        AgentRole.BULL_THESIS: DummyThesisAgent(AgentRole.BULL_THESIS, "bull", 150.0),
        AgentRole.BEAR_THESIS: DummyThesisAgent(AgentRole.BEAR_THESIS, "bear", 90.0),
        AgentRole.MACRO_ECONOMIST: DummySpecialist(AgentRole.MACRO_ECONOMIST),
        AgentRole.RISK_MANAGER: DummySpecialist(AgentRole.RISK_MANAGER),
        AgentRole.PORTFOLIO_MANAGER: DummySpecialist(AgentRole.PORTFOLIO_MANAGER),
    }
    orchestrator = DebateOrchestrator(bus, agents)
    ctx = AgentContext(run_id="test-run", ticker="ACME", bus=bus, memory=InMemoryStore())

    state = await orchestrator.run(
        ctx, independent_roles=[AgentRole.FUNDAMENTAL_ANALYST], current_price=100.0
    )

    assert state.stage == DebateStage.DONE
    assert state.bull_price_target == 150.0
    assert state.bear_price_target == 90.0
    assert not state.errors
    assert "agreement_score" in state.findings


@pytest.mark.asyncio
async def test_debate_run_degrades_gracefully_on_agent_failure():
    bus = MessageBus()
    agents = {
        AgentRole.FUNDAMENTAL_ANALYST: FailingAgent(AgentRole.FUNDAMENTAL_ANALYST),
        AgentRole.BULL_THESIS: DummyThesisAgent(AgentRole.BULL_THESIS, "bull", 150.0),
        AgentRole.BEAR_THESIS: DummyThesisAgent(AgentRole.BEAR_THESIS, "bear", 90.0),
        AgentRole.MACRO_ECONOMIST: DummySpecialist(AgentRole.MACRO_ECONOMIST),
        AgentRole.RISK_MANAGER: DummySpecialist(AgentRole.RISK_MANAGER),
        AgentRole.PORTFOLIO_MANAGER: DummySpecialist(AgentRole.PORTFOLIO_MANAGER),
    }
    orchestrator = DebateOrchestrator(bus, agents)
    ctx = AgentContext(run_id="test-run-2", ticker="ACME", bus=bus, memory=InMemoryStore())

    # Should NOT raise — a single specialist failure must degrade, not crash, the run.
    state = await orchestrator.run(ctx, independent_roles=[AgentRole.FUNDAMENTAL_ANALYST])

    assert state.stage == DebateStage.DONE
    assert len(state.errors) == 1
    assert state.errors[0].agent == AgentRole.FUNDAMENTAL_ANALYST
    # Bull/bear still ran independently of the failed specialist.
    assert state.bull_price_target == 150.0


def test_compute_agreement_bounds():
    assert compute_agreement(100, 100, 100) == pytest.approx(1.0)
    assert compute_agreement(150, 50, 100) == pytest.approx(0.0)
    assert 0.0 <= compute_agreement(120, 80, 100) <= 1.0
