"""
Every factual sentence that reaches a report must be a `Claim` backed by one
or more `EvidenceRef`s, or explicitly tagged UNVERIFIED. This module defines
that contract; `verifier.py` enforces it before a report can be rendered.

This is the single most important file for the platform's core promise
("explainable, evidence-backed, non-hallucinated"). Everything downstream
(reports, confidence scoring, compliance review) reads through this model.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceType(str, Enum):
    SEC_10K = "sec_10k"
    SEC_10Q = "sec_10q"
    SEC_8K = "sec_8k"
    SEC_13F = "sec_13f"
    SEC_FORM4 = "sec_form4_insider"
    EARNINGS_TRANSCRIPT = "earnings_transcript"
    FINANCIAL_STATEMENT = "financial_statement"
    MARKET_DATA = "market_data"
    OPTIONS_CHAIN = "options_chain"
    FED_DATA = "federal_reserve_data"
    ECON_RELEASE = "economic_release"
    NEWS_ARTICLE = "news_article"
    ANALYST_ESTIMATE = "analyst_estimate"
    RESEARCH_PAPER = "research_paper"
    INTERNAL_CALCULATION = "internal_calculation"
    KNOWLEDGE_GRAPH = "knowledge_graph_fact"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"                # resolvable to a live/cached source record
    STALE = "stale"                      # source resolvable but past freshness TTL
    UNVERIFIED = "unverified"             # no resolvable source; must be flagged in report
    CONTRADICTED = "contradicted"         # a newer/higher-priority source disagrees


class EvidenceRef(BaseModel):
    """A pointer to a specific, retrievable piece of source material."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str  # deterministic hash, see `make_evidence_id`
    source_type: SourceType
    source_id: str            # e.g. accession number, transcript id, bar timestamp key
    ticker: str | None = None
    retrieved_at: datetime
    published_at: datetime | None = None
    excerpt: str | None = Field(
        default=None, description="Short exact excerpt (<300 chars) used ONLY for "
        "human audit; never copied verbatim into report prose beyond fair-use snippets."
    )
    excerpt_hash: str | None = None
    url_or_locator: str | None = None
    data_quality_score: float = Field(ge=0.0, le=1.0, default=1.0)

    @model_validator(mode="after")
    def _hash_excerpt(self) -> EvidenceRef:
        if self.excerpt and not self.excerpt_hash:
            object.__setattr__(
                self, "excerpt_hash",
                hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()[:16],
            )
        return self


def make_evidence_id(source_type: SourceType, source_id: str, field: str = "") -> str:
    """Deterministic id so the same underlying fact always maps to the same
    evidence node in the knowledge graph, enabling de-duplication."""
    raw = f"{source_type.value}:{source_id}:{field}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class Claim(BaseModel):
    """One atomic factual or analytical assertion produced by an agent."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    text: str
    made_by: str  # AgentRole value
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    is_numeric: bool = False
    numeric_value: float | None = None
    numeric_source: str | None = Field(
        default=None,
        description="If is_numeric, MUST name the deterministic function/module "
        "that produced numeric_value, e.g. 'quant.dcf.discounted_cash_flow'. "
        "An LLM-authored number with no numeric_source is a hard validation failure.",
    )

    @model_validator(mode="after")
    def _numeric_requires_source(self) -> Claim:
        if self.is_numeric and not self.numeric_source:
            raise ValueError(
                f"Claim {self.claim_id!r} is numeric but has no numeric_source — "
                "numbers must originate from the deterministic quant engine."
            )
        return self


class ClaimBundle(BaseModel):
    """Convenience container an agent returns instead of raw text."""

    claims: list[Claim]
    unverified_count: int = 0

    @model_validator(mode="after")
    def _tally(self) -> ClaimBundle:
        object.__setattr__(
            self, "unverified_count",
            sum(1 for c in self.claims if c.verification_status == VerificationStatus.UNVERIFIED),
        )
        return self
