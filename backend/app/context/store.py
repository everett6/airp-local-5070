"""
`ContextStore` holds compressed chunks per ticker and answers the question
every agent actually has: "give me everything useful about ACME that fits in
N tokens." That question — bounded retrieval instead of an ever-growing
prompt — is the concrete mechanism behind Layer 3's "use retrieval instead
of increasing context length" principle (see docs/ARCHITECTURE.md §4),
applied specifically to per-ticker research material rather than
cross-session memory (app/memory/ handles that; this handles "what do we
know about this ticker right now").

Selection is a greedy knapsack by (importance, recency) — not optimal, but
optimal chunk selection isn't the point here: the guarantee that matters is
"never exceed the budget" and "prefer the most important things when
something has to be cut", both of which greedy selection gives you in O(n
log n) with a result that's easy to reason about and audit (see
ContextPack.truncated / excluded_chunk_ids).

Default implementation is in-process (a dict), matching this repo's
local-first "no external DB required by default" stance (see
core/config.py). Swap for a real vector store behind the same interface if
you want semantic retrieval across a much larger corpus than fits in memory.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.context.compressor import Compressor
from app.context.models import ChunkCategory, CompressedChunk, ContextPack
from app.context.token_estimator import estimate_tokens


class ContextStore:
    def __init__(self) -> None:
        self._chunks: dict[str, list[CompressedChunk]] = {}

    async def add_raw(
        self,
        *,
        ticker: str,
        category: ChunkCategory,
        raw_text: str,
        evidence_ids: list[str],
        compressor: Compressor,
        target_tokens: int = 150,
        importance: float = 0.5,
    ) -> CompressedChunk:
        chunk = await compressor.compress(
            ticker=ticker, category=category, raw_text=raw_text,
            evidence_ids=evidence_ids, target_tokens=target_tokens, importance=importance,
        )
        self._chunks.setdefault(ticker, []).append(chunk)
        return chunk

    def add_chunk(self, chunk: CompressedChunk) -> None:
        """For when a chunk was already produced elsewhere (e.g. a report
        generator archiving its own summary as future context) rather than
        compressed fresh from raw text."""
        self._chunks.setdefault(chunk.ticker, []).append(chunk)

    def prune_stale(self, ticker: str, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        before = len(self._chunks.get(ticker, []))
        self._chunks[ticker] = [c for c in self._chunks.get(ticker, []) if not c.is_stale(now)]
        return before - len(self._chunks[ticker])

    def get_context_pack(
        self,
        ticker: str,
        budget_tokens: int,
        categories: list[ChunkCategory] | None = None,
        min_importance: float = 0.0,
        now: datetime | None = None,
    ) -> ContextPack:
        now = now or datetime.now(UTC)
        candidates = [
            c for c in self._chunks.get(ticker, [])
            if not c.is_stale(now)
            and c.importance >= min_importance
            and (categories is None or c.category in categories)
        ]

        # Greedy: highest importance first, ties broken by recency (newest
        # first) — a simple, auditable priority order rather than an
        # optimization that would need its own justification.
        candidates.sort(key=lambda c: (c.importance, c.created_at), reverse=True)

        selected: list[CompressedChunk] = []
        excluded: list[str] = []
        running_total = 0
        for chunk in candidates:
            if running_total + chunk.token_estimate <= budget_tokens:
                selected.append(chunk)
                running_total += chunk.token_estimate
            else:
                excluded.append(chunk.chunk_id)

        return ContextPack(
            ticker=ticker,
            chunks=selected,
            total_tokens=running_total,
            budget_tokens=budget_tokens,
            truncated=len(excluded) > 0,
            excluded_chunk_ids=excluded,
        )

    def stats(self, ticker: str) -> dict[str, int]:
        chunks = self._chunks.get(ticker, [])
        return {
            "chunk_count": len(chunks),
            "total_raw_chars": sum(c.source_char_count for c in chunks),
            "total_compressed_tokens": sum(c.token_estimate for c in chunks),
            "total_compressed_chars_equivalent": sum(estimate_tokens(c.summary) for c in chunks),
        }
