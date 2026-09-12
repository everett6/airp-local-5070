"""
A single, deliberately-approximate token estimator used everywhere context
budgets are computed. This is NOT a real tokenizer (no tiktoken/sentencepiece
dependency, since the whole point of this platform is running against
arbitrary local models via Ollama, each with its own tokenizer) — it's a
chars-per-token heuristic calibrated for English financial/business prose.

Using one approximation everywhere (rather than "close enough" math scattered
across callers) means budgets are at least *comparable* to each other even
though they're not exact: a context pack that reports "1,200 tokens" and a
model with a "4,096 token" limit are both using the same yardstick, so the
margin of error is consistent rather than compounding differently at each
call site.
"""
from __future__ import annotations

# Empirically, English text averages ~4 characters per GPT/Llama-family BPE
# token, a little higher for financial text (lots of numbers/punctuation
# tokenize less efficiently than prose). This is intentionally conservative
# (slightly over-estimates tokens) so budget checks fail safe — undercounting
# would risk silently exceeding a model's real context window.
_CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def estimate_tokens_for_messages(messages: list[str]) -> int:
    """Sum of per-message estimates plus a small per-message overhead
    (role/formatting tokens that a real chat template adds) — again a
    deliberately rough constant rather than modeling any specific chat
    template exactly."""
    per_message_overhead = 4
    return sum(estimate_tokens(m) + per_message_overhead for m in messages)


def fits_budget(text: str, budget_tokens: int) -> bool:
    return estimate_tokens(text) <= budget_tokens


def truncate_to_budget(text: str, budget_tokens: int) -> str:
    """Hard truncation fallback (character-based, using the same ratio) for
    when a compressor isn't available/configured. Truncates on a word
    boundary where possible so it doesn't cut mid-word."""
    if fits_budget(text, budget_tokens):
        return text
    max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.7:  # only trim to the word boundary if it's not too far back
        truncated = truncated[:last_space]
    return truncated.rstrip() + " […truncated]"
