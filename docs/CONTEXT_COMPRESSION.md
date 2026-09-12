# Context Compression

## The problem this solves

A research pass on one ticker accumulates a lot of source material: several
news articles, filing excerpts, transcript passages, prior theses. Handing
all of that to an LLM verbatim every time an agent needs "what do we know
about ACME" either blows the context window or crowds out the actual
instructions with raw text the model has to re-read every call. This matters
more here than it would calling a frontier hosted API, because local models
on consumer GPUs (a 5070, a 3060) have real, tight context limits and you
pay for every token in latency, not just money.

The fix is the same principle already stated in `docs/ARCHITECTURE.md` §4
("use retrieval instead of increasing context length"), applied specifically
to per-ticker research material: compress source material once into a short
summary, store it, and pull back only as much as fits a token budget when an
agent actually needs it.

## The three pieces (`backend/app/context/`)

1. **`models.py` — `CompressedChunk` / `ContextPack`.** A chunk is one
   compressed piece of source material. Critically, it carries
   `evidence_ids` through unchanged from whatever it was compressed from —
   compression is not allowed to sever the link back to a verifiable source,
   because that link is the whole basis of this platform's evidence model
   (`app/evidence/models.py`). A `ContextPack` is a token-bounded selection
   of chunks for one ticker, with explicit bookkeeping (`truncated`,
   `excluded_chunk_ids`) about what didn't fit — so a caller can tell "this
   is everything" apart from "this is what fit in budget."

2. **`compressor.py` — how raw text becomes a chunk.** Two implementations:
   - `TruncatingCompressor`: no LLM call, deterministic, biases toward
     sentences containing digits (prices, dates, percentages) on the theory
     that in financial text those are disproportionately the load-bearing
     sentences. Always available, cannot fabricate anything since it only
     ever removes text.
   - `LLMSummarizingCompressor`: routes through the "extraction" LLM
     endpoint (see `app/llm/router.py` — summarization is exactly the
     low-ambiguity, speed-over-depth work that endpoint exists for). Produces
     denser, better summaries, but is a synthesis step — the resulting chunk
     is marked `is_llm_generated=True`.
   - `FallbackCompressor` wraps the two: tries the LLM, falls back to
     deterministic truncation on any failure (unreachable endpoint, timeout).
     This mirrors the rest of the platform's fault-tolerance pattern — see
     `debate/orchestrator.py`'s per-stage try/except for the same philosophy
     of "one component failing degrades the run, it doesn't crash it."

3. **`store.py` — `ContextStore`.** Holds chunks per ticker and answers
   "give me everything useful about ACME that fits in N tokens" via a greedy
   selection: sort by `(importance, recency)`, add chunks until the budget
   is exhausted. Not an optimal knapsack solve — deliberately not, since the
   property that actually matters (never exceed budget, prefer the most
   important thing when something has to be cut) is what greedy selection
   guarantees, and it's easy to audit *why* a given chunk was excluded.

## Token estimation is deliberately approximate

`token_estimator.py` uses a chars-per-token heuristic (~3.5), not a real
tokenizer. This is intentional: this platform is designed to run against
whatever local model you point it at via Ollama, and different
model families use different tokenizers. Rather than pull in a specific
tokenizer library that would only be exactly right for one model family, one
approximation is used everywhere token budgets are computed — meaning a
"1,200 token" budget always means the same thing throughout the codebase,
even if it's not the exact number your specific model's tokenizer would
report. The estimate is calibrated to slightly over-count rather than
under-count, so a budget check fails safe (leaves headroom) rather than
risking silently exceeding a model's real context window.

## What this does NOT do (yet)

- **No semantic retrieval.** `ContextStore` is a flat per-ticker list, not a
  vector index — selection is by importance/recency, not by relevance to a
  specific query. For the corpus size this is built for (one ticker's active
  research material, not a full historical archive), this is sufficient;
  swapping in a vector store behind the same interface is a natural
  extension if that stops being true.
- **No automatic importance scoring.** `importance` is currently supplied by
  the caller when a chunk is created (e.g. a Fundamental Analyst's own
  output might be scored higher than a random news blurb) rather than
  learned or inferred. Getting this right well enough to auto-score is
  future work, not something worth guessing at now.
- **Not wired into the agent toolkit yet.** The mechanism is built and
  tested in isolation; wiring `ContextStore.get_context_pack` into the
  actual agent prompt-construction path (so a Bull/Bear thesis agent
  automatically pulls a context pack instead of receiving raw upstream
  findings) is the next integration step — see `docs/ROADMAP.md`.

## Verified

`backend/tests/test_context.py` — 22 tests, including a direct check that
compressing and retrieving a context pack from ten synthetic articles uses
less than half the tokens that concatenating the raw articles would have
(`test_compression_meaningfully_reduces_footprint_vs_raw_concatenation`),
not just that the code runs without error.
