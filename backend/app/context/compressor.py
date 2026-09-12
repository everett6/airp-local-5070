"""
Two ways to turn raw text into a `CompressedChunk`:

- `LLMSummarizingCompressor` — asks an LLM (routed to the "extraction"
  endpoint by default; summarization is exactly the low-ambiguity,
  speed-over-depth work that role exists for, see llm/router.py) to condense
  the text. Produces much better summaries, but is a synthesis step: the
  output is `is_llm_generated=True` and is never treated as independently
  numeric-verifiable.
- `TruncatingCompressor` — no LLM call at all; deterministically extracts the
  first N characters (optionally biased toward sentences containing numbers,
  since financial text front-loads the figures that matter). Always
  available, always instant, and by construction cannot fabricate anything
  since it only ever removes text, never generates it.

Both compressors are required to pass `evidence_ids` through unchanged —
enforced by `_build_chunk` being the only place either one constructs a
`CompressedChunk`, so there's exactly one code path that could get this
wrong instead of two.
"""
from __future__ import annotations

import re
import uuid
from typing import Protocol

from app.context.models import ChunkCategory, CompressedChunk
from app.context.token_estimator import truncate_to_budget
from app.llm.client import ChatMessage, LLMClient

_SUMMARIZE_SYSTEM_PROMPT = """You compress investment-research source material \
into a short, dense summary for another analyst to read instead of the \
original. Rules:
- Only include facts, figures, and statements present in the source text.
- Never add information, context, or interpretation not present in the source.
- Prioritize numbers, named events, dates, and stated risks over general description.
- Write in plain declarative sentences, no preamble, no meta-commentary.
- Target length: approximately {target_words} words."""


class Compressor(Protocol):
    async def compress(
        self,
        *,
        ticker: str,
        category: ChunkCategory,
        raw_text: str,
        evidence_ids: list[str],
        target_tokens: int,
        importance: float = 0.5,
    ) -> CompressedChunk: ...


def _build_chunk(
    ticker: str,
    category: ChunkCategory,
    summary: str,
    evidence_ids: list[str],
    source_char_count: int,
    importance: float,
    is_llm_generated: bool,
) -> CompressedChunk:
    return CompressedChunk(
        chunk_id=str(uuid.uuid4()),
        ticker=ticker,
        category=category,
        summary=summary.strip(),
        evidence_ids=list(evidence_ids),  # copy — never the same list object as the caller's
        source_char_count=source_char_count,
        importance=importance,
        is_llm_generated=is_llm_generated,
    )


class TruncatingCompressor:
    """Zero-dependency fallback. Biases toward sentences containing digits
    (prices, percentages, dates) on the theory that in financial text, the
    numeric sentences are disproportionately the load-bearing ones — a crude
    but genuinely useful heuristic, not just "take the first N characters"."""

    async def compress(
        self,
        *,
        ticker: str,
        category: ChunkCategory,
        raw_text: str,
        evidence_ids: list[str],
        target_tokens: int,
        importance: float = 0.5,
    ) -> CompressedChunk:
        sentences = re.split(r"(?<=[.!?])\s+", raw_text.strip())
        numeric_sentences = [s for s in sentences if re.search(r"\d", s)]
        other_sentences = [s for s in sentences if not re.search(r"\d", s)]

        ordered = numeric_sentences + other_sentences  # numeric first, then filler
        assembled = ""
        for sentence in ordered:
            candidate = f"{assembled} {sentence}".strip()
            if len(candidate) > int(target_tokens * 3.5):
                break
            assembled = candidate

        if not assembled:
            assembled = raw_text

        summary = truncate_to_budget(assembled, target_tokens)
        return _build_chunk(
            ticker, category, summary, evidence_ids, len(raw_text), importance,
            is_llm_generated=False,
        )


class LLMSummarizingCompressor:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def compress(
        self,
        *,
        ticker: str,
        category: ChunkCategory,
        raw_text: str,
        evidence_ids: list[str],
        target_tokens: int,
        importance: float = 0.5,
    ) -> CompressedChunk:
        target_words = max(15, int(target_tokens * 0.7))  # ~0.7 words per token, rough
        messages = [
            ChatMessage(role="system", content=_SUMMARIZE_SYSTEM_PROMPT.format(target_words=target_words)),
            ChatMessage(role="user", content=f"Ticker: {ticker}\nCategory: {category.value}\n\nSource text:\n{raw_text}"),
        ]
        response = await self._llm.chat(messages, temperature=0.1, max_tokens=target_tokens * 2)
        summary = truncate_to_budget(response.text, target_tokens)  # belt-and-suspenders: enforce budget even if the model ignores the word target

        return _build_chunk(
            ticker, category, summary, evidence_ids, len(raw_text), importance,
            is_llm_generated=True,
        )


class FallbackCompressor:
    """Tries the LLM compressor; if the endpoint is unreachable (see
    llm/client.py's LLMError), falls back to the deterministic truncator
    rather than failing the whole context-building step. This mirrors the
    rest of the platform's fault-tolerance pattern (a single agent/connector
    failure degrades, it doesn't crash the run) — see
    debate/orchestrator.py's per-stage try/except for the same philosophy."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_compressor = LLMSummarizingCompressor(llm_client)
        self._fallback = TruncatingCompressor()

    async def compress(
        self,
        *,
        ticker: str,
        category: ChunkCategory,
        raw_text: str,
        evidence_ids: list[str],
        target_tokens: int,
        importance: float = 0.5,
    ) -> CompressedChunk:
        try:
            return await self._llm_compressor.compress(
                ticker=ticker, category=category, raw_text=raw_text,
                evidence_ids=evidence_ids, target_tokens=target_tokens, importance=importance,
            )
        except Exception:  # noqa: BLE001 — any LLM failure degrades, never crashes context building
            return await self._fallback.compress(
                ticker=ticker, category=category, raw_text=raw_text,
                evidence_ids=evidence_ids, target_tokens=target_tokens, importance=importance,
            )
