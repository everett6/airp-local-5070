from datetime import UTC, datetime, timedelta

import pytest

from app.context.compressor import (
    FallbackCompressor,
    LLMSummarizingCompressor,
    TruncatingCompressor,
)
from app.context.models import ChunkCategory, CompressedChunk
from app.context.store import ContextStore
from app.context.token_estimator import (
    estimate_tokens,
    estimate_tokens_for_messages,
    fits_budget,
    truncate_to_budget,
)
from app.llm.client import ChatMessage, ChatResponse

# ── token estimator ─────────────────────────────────────────────────────

def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("hello")
    long = estimate_tokens("hello " * 100)
    assert long > short
    assert short >= 1


def test_estimate_tokens_empty_string():
    assert estimate_tokens("") == 0


def test_fits_budget_boundary():
    text = "a" * 35  # ~10 tokens at 3.5 chars/token
    assert fits_budget(text, 10)
    assert not fits_budget(text, 5)


def test_truncate_to_budget_respects_word_boundary():
    text = "The quarterly revenue grew significantly due to strong demand across all regions"
    truncated = truncate_to_budget(text, budget_tokens=8)
    assert truncated.endswith("[…truncated]")
    assert not truncated.startswith(" ")
    # must not exceed budget by much even after appending the marker
    assert estimate_tokens(truncated) <= 8 + estimate_tokens(" […truncated]") + 2


def test_estimate_tokens_for_messages_includes_overhead():
    single = estimate_tokens_for_messages(["hello"])
    assert single > estimate_tokens("hello")  # overhead added


# ── deterministic compressor ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_truncating_compressor_preserves_evidence_ids():
    compressor = TruncatingCompressor()
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.NEWS,
        raw_text="ACME reported revenue of $500M, up 12% year over year. The CEO commented on strong demand.",
        evidence_ids=["ev1", "ev2"], target_tokens=20,
    )
    assert chunk.evidence_ids == ["ev1", "ev2"]
    assert chunk.is_llm_generated is False
    assert chunk.ticker == "ACME"
    assert chunk.category == ChunkCategory.NEWS


@pytest.mark.asyncio
async def test_truncating_compressor_prioritizes_numeric_sentences():
    raw = (
        "The company has a long and storied history in the industry going back decades. "
        "Revenue grew 45% to $2.1B in the quarter. "
        "Many analysts follow this company closely. "
        "Net income was $300M, beating estimates of $250M."
    )
    compressor = TruncatingCompressor()
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.NEWS, raw_text=raw,
        evidence_ids=[], target_tokens=15,
    )
    # With a tight budget, the numeric sentences should be prioritized over
    # the generic filler sentences.
    assert "45%" in chunk.summary or "2.1B" in chunk.summary or "300M" in chunk.summary


@pytest.mark.asyncio
async def test_truncating_compressor_never_exceeds_budget_by_much():
    raw = "word " * 500
    compressor = TruncatingCompressor()
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.NEWS, raw_text=raw,
        evidence_ids=[], target_tokens=10,
    )
    assert chunk.token_estimate <= 10 + 5  # small slack for the truncation marker


@pytest.mark.asyncio
async def test_compression_never_adds_evidence_ids_not_given():
    compressor = TruncatingCompressor()
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.FILING, raw_text="Some filing text.",
        evidence_ids=[], target_tokens=20,
    )
    assert chunk.evidence_ids == []  # never invented from nothing


# ── LLM-backed compressor (fake client, no network) ─────────────────────

class FakeLLMClient:
    """Deterministic stand-in for LLMClient — no real network call, so this
    test suite runs fully offline like the rest of the repo's tests."""

    def __init__(self, response_text: str = "Compressed: revenue up 12%.") -> None:
        self.response_text = response_text
        self.calls: list[list[ChatMessage]] = []

    async def chat(self, messages, *, json_mode=False, temperature=0.2, max_tokens=2048) -> ChatResponse:
        self.calls.append(messages)
        return ChatResponse(text=self.response_text, model="fake", endpoint_name="fake")

    async def chat_json(self, messages, *, temperature=0.2, max_tokens=2048, max_repair_attempts=2):
        return {}


class FailingLLMClient:
    async def chat(self, messages, *, json_mode=False, temperature=0.2, max_tokens=2048):
        raise RuntimeError("endpoint unreachable")

    async def chat_json(self, messages, *, temperature=0.2, max_tokens=2048, max_repair_attempts=2):
        raise RuntimeError("endpoint unreachable")


@pytest.mark.asyncio
async def test_llm_summarizing_compressor_uses_llm_and_preserves_evidence():
    fake = FakeLLMClient("Revenue rose 12% to $500M on strong demand.")
    compressor = LLMSummarizingCompressor(fake)
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.NEWS, raw_text="long raw article text " * 50,
        evidence_ids=["ev-news-1"], target_tokens=30,
    )
    assert chunk.is_llm_generated is True
    assert chunk.evidence_ids == ["ev-news-1"]
    assert "12%" in chunk.summary
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_fallback_compressor_degrades_to_deterministic_on_llm_failure():
    failing = FailingLLMClient()
    compressor = FallbackCompressor(failing)
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.NEWS,
        raw_text="Revenue grew 20% to $100M in the quarter.",
        evidence_ids=["ev1"], target_tokens=20,
    )
    # Must NOT raise, and must still produce a usable, evidence-preserving chunk.
    assert chunk.is_llm_generated is False
    assert chunk.evidence_ids == ["ev1"]


@pytest.mark.asyncio
async def test_fallback_compressor_uses_llm_when_available():
    fake = FakeLLMClient("Short summary.")
    compressor = FallbackCompressor(fake)
    chunk = await compressor.compress(
        ticker="ACME", category=ChunkCategory.NEWS, raw_text="raw text " * 20,
        evidence_ids=[], target_tokens=20,
    )
    assert chunk.is_llm_generated is True


# ── ContextStore: the actual budget-enforcement mechanism ───────────────

def make_chunk(ticker="ACME", importance=0.5, tokens=50, category=ChunkCategory.NEWS,
                created_at=None, chunk_id=None) -> CompressedChunk:
    return CompressedChunk(
        chunk_id=chunk_id or f"chunk-{importance}-{tokens}-{category.value}",
        ticker=ticker, category=category, summary="x" * (tokens * 4),
        evidence_ids=[], source_char_count=1000, token_estimate=tokens,
        importance=importance, is_llm_generated=False,
        created_at=created_at or datetime.now(UTC),
    )


def test_context_pack_stays_within_budget():
    store = ContextStore()
    for i in range(10):
        store.add_chunk(make_chunk(importance=0.5, tokens=50, chunk_id=f"c{i}"))

    pack = store.get_context_pack("ACME", budget_tokens=120)
    assert pack.total_tokens <= 120
    assert pack.truncated is True
    assert len(pack.excluded_chunk_ids) > 0


def test_context_pack_prioritizes_higher_importance():
    store = ContextStore()
    store.add_chunk(make_chunk(importance=0.9, tokens=50, chunk_id="high"))
    store.add_chunk(make_chunk(importance=0.1, tokens=50, chunk_id="low"))

    pack = store.get_context_pack("ACME", budget_tokens=50)  # only room for one
    assert len(pack.chunks) == 1
    assert pack.chunks[0].chunk_id == "high"
    assert "low" in pack.excluded_chunk_ids


def test_context_pack_filters_by_category():
    store = ContextStore()
    store.add_chunk(make_chunk(category=ChunkCategory.NEWS, chunk_id="news1"))
    store.add_chunk(make_chunk(category=ChunkCategory.FILING, chunk_id="filing1"))

    pack = store.get_context_pack("ACME", budget_tokens=1000, categories=[ChunkCategory.FILING])
    assert len(pack.chunks) == 1
    assert pack.chunks[0].category == ChunkCategory.FILING


def test_context_pack_excludes_stale_chunks():
    store = ContextStore()
    old_chunk = make_chunk(chunk_id="old")
    object.__setattr__(old_chunk, "stale_after_seconds", 1)  # frozen model, bypass for test setup
    object.__setattr__(old_chunk, "created_at", datetime.now(UTC) - timedelta(hours=1))
    store.add_chunk(old_chunk)
    store.add_chunk(make_chunk(chunk_id="fresh"))

    pack = store.get_context_pack("ACME", budget_tokens=1000)
    ids = [c.chunk_id for c in pack.chunks]
    assert "old" not in ids
    assert "fresh" in ids


def test_context_pack_min_importance_filter():
    store = ContextStore()
    store.add_chunk(make_chunk(importance=0.2, chunk_id="low"))
    store.add_chunk(make_chunk(importance=0.8, chunk_id="high"))

    pack = store.get_context_pack("ACME", budget_tokens=1000, min_importance=0.5)
    ids = [c.chunk_id for c in pack.chunks]
    assert "low" not in ids
    assert "high" in ids


def test_context_pack_empty_ticker_returns_empty_pack():
    store = ContextStore()
    pack = store.get_context_pack("NOPE", budget_tokens=500)
    assert pack.chunks == []
    assert pack.truncated is False
    assert "no cached context" in pack.as_prompt_text()


def test_context_pack_as_prompt_text_includes_evidence_refs():
    store = ContextStore()
    chunk = make_chunk(chunk_id="c1")
    object.__setattr__(chunk, "evidence_ids", ["ev-abc"])
    store.add_chunk(chunk)

    pack = store.get_context_pack("ACME", budget_tokens=1000)
    text = pack.as_prompt_text()
    assert "ev-abc" in text


@pytest.mark.asyncio
async def test_add_raw_compresses_and_stores_in_one_step():
    store = ContextStore()
    compressor = TruncatingCompressor()
    chunk = await store.add_raw(
        ticker="ACME", category=ChunkCategory.NEWS,
        raw_text="ACME shares rose 5% after strong earnings beat estimates by $0.10.",
        evidence_ids=["ev1"], compressor=compressor, target_tokens=15, importance=0.7,
    )
    assert chunk.ticker == "ACME"
    stats = store.stats("ACME")
    assert stats["chunk_count"] == 1
    assert stats["total_raw_chars"] > 0


def test_prune_stale_removes_only_expired():
    store = ContextStore()
    fresh = make_chunk(chunk_id="fresh")
    stale = make_chunk(chunk_id="stale")
    object.__setattr__(stale, "stale_after_seconds", 1)
    object.__setattr__(stale, "created_at", datetime.now(UTC) - timedelta(hours=1))
    store.add_chunk(fresh)
    store.add_chunk(stale)

    removed = store.prune_stale("ACME")
    assert removed == 1
    remaining_ids = [c.chunk_id for c in store._chunks["ACME"]]
    assert remaining_ids == ["fresh"]


# ── end-to-end: compression actually reduces size vs. raw context ───────

@pytest.mark.asyncio
async def test_compression_meaningfully_reduces_footprint_vs_raw_concatenation():
    """The concrete claim being tested: storing N compressed chunks and
    pulling a context pack uses far fewer tokens than concatenating the raw
    source material would have."""
    store = ContextStore()
    compressor = TruncatingCompressor()
    raw_articles = [
        f"Article {i}: ACME Corp announced quarterly results today. " * 30
        for i in range(10)
    ]
    for i, article in enumerate(raw_articles):
        await store.add_raw(
            ticker="ACME", category=ChunkCategory.NEWS, raw_text=article,
            evidence_ids=[f"ev{i}"], compressor=compressor, target_tokens=30,
        )

    raw_total_tokens = sum(estimate_tokens(a) for a in raw_articles)
    pack = store.get_context_pack("ACME", budget_tokens=10_000)  # generous budget, get everything

    assert pack.total_tokens < raw_total_tokens
    assert pack.total_tokens < raw_total_tokens * 0.5  # meaningfully smaller, not just marginally
