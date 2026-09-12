"""
A `CompressedChunk` is what a raw piece of research material (a news
article, a filing section, a transcript excerpt) becomes after compression:
short enough to fit many of them in a prompt, but still carrying the
`evidence_ids` that trace back to the original source — compression must
never sever that link, or every claim built from compressed context becomes
unverifiable by construction (a direct violation of the evidence model in
app/evidence/models.py).

This is why compression here is NOT "throw the raw text at an LLM and keep
whatever prose comes back forever" — every chunk records `source_char_count`
so compression ratio is auditable, and the compressor (see compressor.py) is
required to pass `evidence_ids` through unchanged rather than being able to
invent or drop them.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.context.token_estimator import estimate_tokens


class ChunkCategory(str, Enum):
    FUNDAMENTALS = "fundamentals"
    NEWS = "news"
    FILING = "filing"
    TRANSCRIPT = "earnings_transcript"
    TECHNICAL = "technical"
    PRIOR_THESIS = "prior_thesis"
    ANALYST_ESTIMATE = "analyst_estimate"
    INSIDER_ACTIVITY = "insider_activity"


class CompressedChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    ticker: str
    category: ChunkCategory
    summary: str
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Carried through unchanged from the source material's evidence "
        "refs. A compressor MUST NOT add ids not present in the input and MUST NOT "
        "drop all ids unless the source itself had none.",
    )
    source_char_count: int
    token_estimate: int = 0
    importance: float = Field(ge=0.0, le=1.0, default=0.5)
    is_llm_generated: bool = Field(
        description="True if an LLM produced `summary` (a synthesis, verified only "
        "for internal consistency — see evidence/verifier.py's treatment of "
        "non-numeric synthesis claims). False if `summary` is a deterministic "
        "truncation/extraction of the source, which is at least guaranteed to "
        "contain no fabricated content since nothing was generated."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stale_after_seconds: int = 3600 * 24 * 7  # default: treat as stale after a week

    @model_validator(mode="after")
    def _compute_token_estimate(self) -> CompressedChunk:
        if self.token_estimate == 0:
            object.__setattr__(self, "token_estimate", estimate_tokens(self.summary))
        return self

    @property
    def compression_ratio(self) -> float:
        if self.source_char_count == 0:
            return 1.0
        return round(len(self.summary) / self.source_char_count, 4)

    def is_stale(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        age_seconds = (now - self.created_at).total_seconds()
        return age_seconds > self.stale_after_seconds


class ContextPack(BaseModel):
    """What an agent actually receives: a token-bounded selection of chunks
    for one ticker, plus bookkeeping about what got left out so a caller
    can tell "this is everything we know" apart from "this is what fit"."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    chunks: list[CompressedChunk]
    total_tokens: int
    budget_tokens: int
    truncated: bool
    excluded_chunk_ids: list[str] = Field(default_factory=list)

    def as_prompt_text(self) -> str:
        """Render the pack as plain text suitable for insertion into an LLM
        prompt. Each chunk is labeled with its category and evidence ids so a
        downstream synthesis agent can still cite specific sources even
        though it's reading a compressed summary, not the raw material."""
        if not self.chunks:
            return f"(no cached context available for {self.ticker})"
        lines = [f"Context for {self.ticker} ({len(self.chunks)} items, ~{self.total_tokens} tokens):"]
        for c in self.chunks:
            evidence_note = f" [evidence: {', '.join(c.evidence_ids)}]" if c.evidence_ids else " [no evidence ref]"
            lines.append(f"- ({c.category.value}) {c.summary}{evidence_note}")
        if self.truncated:
            lines.append(
                f"[{len(self.excluded_chunk_ids)} additional lower-priority item(s) omitted "
                f"to stay within the {self.budget_tokens}-token budget]"
            )
        return "\n".join(lines)


CompressionStrategy = Literal["llm_summarize", "deterministic_truncate"]
