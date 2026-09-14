# Execution plan — airp-local-5070

Status as of 2026-09-13. Done so far: the jailed walk-forward, runs v1–v4, the
write-up, the Streamlit dashboard, and week-clustered significance tests. Every
result so far says the same thing: **no edge over always predicting "up"**.

This plan has one goal: give an edge a fair chance to show up, under tests
strict enough that we'd trust a positive result. It also defines, in advance,
what counts as success and what we do if nothing works.

## Principles

- **Fix the success criteria before running.** Every new experiment gets a
  frozen config committed *before* its first run. Results that required
  changing that config don't count.
- **Every result carries its receipts:** git commit, config hash, data hash,
  and Ollama model digest are stored in the results JSON.
- **The GPU runs overnight.** Human-time work (code, reviews) runs in parallel
  with GPU runs.
- **Each phase ends green:** tests, ruff, and mypy on touched modules pass, the
  dashboard loads, and the changes are pushed.

---

## Phase A — Provenance and reproducibility (~2 h). ✅ Done 2026-09-13 (commit `ec59aff`)

Everything after this produces new results, so they must be stamped from the start.

| # | Task | Done when |
|---|---|---|
| A1 | Record `git_commit`, `dirty` flag, sha256 of `prices.csv`, and the Ollama model digest (from `/api/tags`) in every results JSON | ✅ Fields present; dashboard Overview has a Receipts panel |
| A2 | `--config configs/<name>.toml` for walkforward. The runner stores the config hash and refuses to write over a finished run with a different hash | ✅ Tested, and checked on the real CLI |
| A3 | `scripts/reproduce.py`: verify the pinned data checksum (optionally fetch), re-run every frozen config from the committed cache, diff every score and prediction | ✅ Exits 0, "ALL IDENTICAL" for v2, v3_excess, v4_14b; negative control detected |
| A4 | Jail hardening: memory/CPU limits (`prlimit`), per-call timeout, max message size | ✅ 17 hostile-worker tests (memory hog, hang, oversized message, request flood, bad JSON, stderr flood), jailed and unjailed |

## Phase W — Live web tools. ✅ Done 2026-09-13 (added at your request)

The jailed agent can research in real time through guarded tools (news, articles, prices,
SEC filings, sandboxed Python). Live-only by design. See `docs/LIVE_TOOLS.md`. This changes B3:
the forward test logs **two** arms each week, price-only (`live_plain`) and web-informed
(`live_web`), so we learn whether web information actually helps, measured before outcomes exist.

## Phase B — Airtight evaluation (~3 h of work, plus calendar time)

| # | Task | Done when |
|---|---|---|
| B1 | Memorization probe for qwen3:14b (~5 min GPU) | Both models on the dashboard chart; the doc says whether 14B's window start is still valid |
| B2 | **Survivorship fix:** pick the universe *as of the window start*, e.g. the 20 largest S&P 500 members on 2025-06-01 by market cap, from a dated snapshot. Don't use today's winners | `data/universe_2025-06-01.csv` committed with its source; v2 re-run on it (v2b) |
| B3 | **Pre-registered forward test.** Freeze the best current config as `configs/forward_v1.toml`. A weekly job (Monday after close) fetches prices, predicts the next 5 days, and appends to a write-once log where each entry holds the previous entry's hash. Resolved outcomes are filled in later | First week's predictions are logged with timestamps *before* the outcomes exist. The dashboard has a "Live" tab |
| B4 | Doc update: v2b plus the forward-test protocol | Pushed |

B3 runs as a catch-up job because the PC isn't always on (see Decisions). The
forward test needs **8–12 weeks** before its numbers mean anything.

## Phase C — New signal: SEC filings and earnings (~1–1.5 days, runs overnight)

This is the main lever: the positive results in the literature come from text,
not price series.

| # | Task | Done when |
|---|---|---|
| C1 | **EDGAR connector:** filing index (8-K, 10-Q, 10-K) with *acceptance timestamps*, text sections (MD&A, 8-K items), polite rate limiting, local cache. Point-in-time rule: `accepted_at <= cutoff close` | Tests: a filing accepted after a cutoff is invisible inside that cutoff's sandbox, including through the cache |
| C2 | **Earnings surprise from XBRL** (companyfacts API), dated by filing date | A per-cutoff "latest reported quarter" feature with no future quarters (tested) |
| C3 | **Text anonymization:** replace company names, tickers, people, and dates with placeholders. Leak probe: ask the model to name the company from the anonymized text | Identification rate reported. If it's above ~20%, results are flagged as possibly memorized |
| C4 | Pass filings to the jailed agent (summaries only), with `num_ctx` 8192. Check VRAM on the 5070 | 8B still 100% on GPU; the jail probe still passes |
| C5 | **Stronger non-LLM baseline:** post-earnings-announcement drift (a surprise-sign rule), so the LLM must beat a known effect, not only coin flips | New arm in results and dashboard |
| C6 | **New targets:** 20-day horizon (non-overlapping steps), and cross-sectional ranking on a ~100-stock universe scored by rank IC (Spearman), plus a top-minus-bottom quintile return | Scoring functions tested on synthetic data with a known IC |
| C7 | Write frozen configs `v5_filings_5d` and `v6_filings_rank20d` **before** running, then run both (~1–2 h GPU each on 8B) | Results JSON has provenance; dashboard shows them |

**Success criteria (set now, before any Phase C result exists):**
1. Rank IC's week-clustered 95% CI is above 0 on the post-warm-up window, **and**
2. It beats the post-earnings-drift baseline on the same predictions (paired CI above 0), **and**
3. The leak-probe identification rate is under 20%.

All three must hold. Anything else is reported as "no edge".

## Phase D — Engineering hygiene (~2–3 h, runs alongside B/C)

| # | Task | Done when |
|---|---|---|
| D1 | GitHub Actions: pytest, ruff, and mypy on `sandbox`, `data_ingestion`, `dashboard/data.py`. Bubblewrap tests skip on CI, since hosted runners restrict user namespaces | Green check on push; badge in README |
| D2 | mypy: 28 errors across the repo (20 in the dashboard app from untyped Streamlit/Altair calls; 8 in `memory`, 4 in `debate`, 3 in `knowledge_graph`, 2 in `agents`, all inherited from upstream). Fix them, or exclude the Streamlit script and fix the rest | `mypy app` clean, or README states exactly what's checked |
| D3 | README "Verified state": remove upstream claims not re-verified here | Every claim was re-run on this PC |
| D4 | Dashboard: provenance panel, Live tab (B3), and the new target types (rank IC chart) | Headless smoke test covers every tab and every run |

## Phase E — Decision gate (~1 h)

- **If Phase C meets all three criteria:** don't trust it yet. Add it to the
  forward test (B3) and wait 8–12 weeks. Only a forward-test pass counts.
- **If it doesn't:** publish the negative result clearly, and stop spending
  GPU time on price-direction prediction. Refocus the project on what LLMs
  demonstrably do well here, using the same point-in-time pipeline: a filings
  summarizer and research assistant that doesn't need to predict prices.

---

## Order and timeline

| Step | Work | GPU | Calendar |
|---|---|---|---|
| A (provenance) | 2 h | — | day 1 |
| B1, B2 | 1.5 h | ~1 h | day 1 |
| D1–D3 (alongside) | 2 h | — | day 1–2 |
| C1–C6 | 1 day | short tests | day 2–3 |
| C7 runs | 30 min | 2–4 h (overnight) | night of day 3 |
| B3 forward test | 1.5 h to set up | ~10 min/week | then 8–12 weeks |
| E decision | 1 h | — | after C7 (first read), after forward test (final) |

## Decisions (answered 2026-09-13)

1. **SEC contact:** a User-Agent with name and email is set; see the EDGAR connector (C1).
2. **Scheduled job:** the PC can't be guaranteed on Monday evenings. So B3 must
   run whenever the PC is next on (e.g. a systemd user timer with
   `Persistent=true`). A week whose prediction couldn't be made before its
   cutoff's next market open is logged as **missed**, never backfilled.
3. **Pushes:** approved at the end of each phase.
