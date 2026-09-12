"""
Fundamental Analyst: reads normalized financial statements + the knowledge
graph, calls the quant/ratios and quant/dcf tools, and returns a structured
`FundamentalFinding` with every number traced to its source function.

This file is the canonical example other specialist agents should follow —
see docs/AGENT_INTERFACES.md for the checklist every new agent must satisfy.
"""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, SpecialistAgent
from app.core.message_protocol import AgentRole, register_payload
from app.evidence.models import Claim, ClaimBundle, EvidenceRef, SourceType, make_evidence_id


class FundamentalAnalysisRequest(BaseModel):
    ticker: str
    fiscal_period: str  # e.g. "FY2025Q2"


@register_payload
class FundamentalFinding(BaseModel):
    ticker: str
    fiscal_period: str
    gross_margin: float
    operating_margin: float
    net_margin: float
    return_on_equity: float
    free_cash_flow: float
    dcf_fair_value_per_share: float
    dcf_discount_rate: float
    dcf_terminal_growth: float
    narrative_summary: str = Field(
        description="Prose synthesis. Every number in this string must also "
        "appear as a structured field above with a matching Claim — prose is "
        "for readability, not as the source of truth."
    )


class FundamentalAnalystAgent(SpecialistAgent[FundamentalAnalysisRequest, FundamentalFinding]):
    role = AgentRole.FUNDAMENTAL_ANALYST

    async def analyze(
        self, ctx: AgentContext, request: FundamentalAnalysisRequest
    ) -> tuple[FundamentalFinding, ClaimBundle]:
        statements = await self.call_tool(
            "get_normalized_financials", ticker=request.ticker, fiscal_period=request.fiscal_period
        )
        source_id = statements["source_id"]
        retrieved_at = datetime.now(UTC)

        gm = await self.call_tool(
            "ratio_gross_margin", revenue=statements["revenue"], cogs=statements["cogs"]
        )
        om = await self.call_tool(
            "ratio_operating_margin",
            operating_income=statements["operating_income"], revenue=statements["revenue"],
        )
        nm = await self.call_tool(
            "ratio_net_margin", net_income=statements["net_income"], revenue=statements["revenue"]
        )
        roe = await self.call_tool(
            "ratio_roe",
            net_income=statements["net_income"],
            avg_shareholders_equity=statements["avg_shareholders_equity"],
        )
        fcf = await self.call_tool(
            "ratio_fcf",
            operating_cash_flow=statements["operating_cash_flow"], capex=statements["capex"],
        )

        dcf = await self.call_tool(
            "dcf_valuation",
            base_free_cash_flow=fcf,
            projection_years=5,
            growth_rates=statements.get("projected_growth_rates", [0.08, 0.07, 0.06, 0.05, 0.04]),
            terminal_growth_rate=0.025,
            discount_rate=statements.get("wacc", 0.09),
            net_debt=statements["net_debt"],
            shares_outstanding=statements["shares_outstanding"],
        )

        ev_ref = EvidenceRef(
            evidence_id=make_evidence_id(SourceType.FINANCIAL_STATEMENT, source_id),
            source_type=SourceType.FINANCIAL_STATEMENT,
            source_id=source_id,
            ticker=request.ticker,
            retrieved_at=retrieved_at,
            data_quality_score=statements.get("quality_score", 0.9),
        )

        def numeric_claim(cid: str, label: str, value: float, source_fn: str) -> Claim:
            return Claim(
                claim_id=f"{request.ticker}:{request.fiscal_period}:{cid}",
                text=f"{label} for {request.ticker} ({request.fiscal_period}) is {value:.4f}",
                made_by=self.role.value,
                evidence_refs=[ev_ref],
                is_numeric=True,
                numeric_value=value,
                numeric_source=source_fn,
            )

        claims = [
            numeric_claim("gross_margin", "Gross margin", gm, "quant.ratios.gross_margin"),
            numeric_claim("operating_margin", "Operating margin", om, "quant.ratios.operating_margin"),
            numeric_claim("net_margin", "Net margin", nm, "quant.ratios.net_margin"),
            numeric_claim("roe", "Return on equity", roe, "quant.ratios.return_on_equity"),
            numeric_claim("fcf", "Free cash flow", fcf, "quant.ratios.free_cash_flow"),
            numeric_claim(
                "dcf_fv", "DCF fair value/share", dcf["fair_value_per_share"],
                "quant.dcf.discounted_cash_flow",
            ),
        ]

        finding = FundamentalFinding(
            ticker=request.ticker,
            fiscal_period=request.fiscal_period,
            gross_margin=gm,
            operating_margin=om,
            net_margin=nm,
            return_on_equity=roe,
            free_cash_flow=fcf,
            dcf_fair_value_per_share=dcf["fair_value_per_share"],
            dcf_discount_rate=statements.get("wacc", 0.09),
            dcf_terminal_growth=0.025,
            narrative_summary=(
                f"{request.ticker} shows a {gm:.1%} gross margin and {om:.1%} operating "
                f"margin in {request.fiscal_period}, with ROE of {roe:.1%}. A 5-year DCF "
                f"using a {statements.get('wacc', 0.09):.1%} discount rate and 2.5% "
                f"terminal growth implies a fair value of ${dcf['fair_value_per_share']:.2f}/share."
            ),
        )
        return finding, ClaimBundle(claims=claims)
