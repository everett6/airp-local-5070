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

## Phase B — Airtight evaluation. ✅ Done 2026-09-14

| # | Task | Result |
|---|---|---|
| B1 | Memorization probe for qwen3:14b | ✅ Done. Found and fixed a scoring bias: unparseable answers counted as "not memorized". Rescored from cache; the 2025-06-02 start holds for both models (`docs/WALKFORWARD_5070.md`) |
| B2 | Survivorship-free universe + v2b re-run | ✅ S&P 500 as of 2025-06-02 (dated Wikipedia revision), top 20 by prior-year dollar volume. v2b: nothing beats always-up, same as v2 |
| B3 | Pre-registered forward test | ✅ Hash-chained ledger, missed weeks never backfilled, catch-up systemd timer. First decision logged on time 2026-09-14 07:30 UTC and pushed before any outcome existed. Dashboard Live tab shows the verified ledger |
| B4 | Docs | ✅ Probe fix, v2b, forward-test protocol |

The forward test needs **8–12 scored weeks** before its numbers mean anything.

## Revision 2026-09-14: what changed and why

Web research before Phase C (sources in the executive summary) changed three things:
1. **Earnings drift in large caps is weak for the latest quarter alone, but
   multi-quarter surprise history still predicts returns.** The drift baseline
   (C5) and the features use the last several standardized surprises, not one.
2. **LLM + reinforcement learning literature adjusts a frozen LLM's outputs
   with a trained policy** (PPO-style post-hoc adjustment) rather than retraining
   the LLM. That fits a 12 GB card and avoids the LLM memorizing the test window
   (retraining its weights on 2025–26 outcomes would do exactly that). Phase R below.
3. **Free-text filing excerpts reveal company identity** (product names, segments),
   which would break anonymization. Phase C starts with a numbers-only, point-in-time
   fundamentals digest and *measures* identity leakage. Masked text excerpts are an
   explicit follow-up, allowed only if their leak probe passes.

More data for learning: the universe grows from 20 to **100** point-in-time S&P 500
members, 5× more predictions per week on the same dates.

## Phase C — Filings and earnings, point-in-time. ✅ Built 2026-09-14 (results below once runs finish)

| # | Task | Done when |
|---|---|---|
| C0 | Point-in-time top-100 universe and prices (same rule as B2) | `data/universe_2025-06-02_top100.csv` + prices, meta lists unrankable members |
| C1 | **EDGAR data layer**: ticker→CIK, submissions (with acceptance timestamps, paged older files), XBRL companyfacts; SEC pacing ≤ 8 req/s, User-Agent from local `.env` only, disk cache | Tests: a fact or filing accepted after a cutoff's 16:00 ET close is invisible at that cutoff, including values later restated |
| C2 | **Fundamentals digest per (stock, cutoff)**: quarterly EPS and revenue as known at the cutoff (latest filed value per period), SUE (seasonal random walk, standardized by the prior 8 changes) for the last 4 quarters, revenue and EPS YoY, days since the last earnings filing, 8-K count in the last 30 days, and whether an earnings report is expected inside the horizon (from last year's filing calendar) | Unit tests on synthetic facts incl. Q4 = FY − 9M derivation and restatements |
| C3 | **Leak probe**: ask the model to name the company from the anonymized digest + prices, for a sample of (stock, cutoff) | Identification rate reported. The digest is only used if it's under 20% |
| C4 | `llm_fund` arm: same jailed agent, prompt adds the digest (numbers only) | Jail probe passes; 8B stays 100% on GPU |
| C5 | **Multi-quarter earnings-surprise baseline** (`sue_rule`, no LLM) and fundamentals in `feat_logit` | New arms in results and dashboard |
| C6 | **New scores**: cross-sectional rank IC per week with a week-clustered CI, top-minus-bottom quintile return, 20-day horizon config | Scoring tested on synthetic data with a known IC |
| C7 | Frozen configs **before** running: `v5_fund_top100` (5-day, weekly, 100 stocks, all arms incl. Phase R) and `v6_fund_rank20d` (20-day, non-overlapping) | Results with receipts; dashboard shows them |

## Phase R — Deep reinforcement learning on top of the jailed LLM (new). ✅ Built 2026-09-14

| # | Task | Done when |
|---|---|---|
| R1 | `rl_agent` (numpy, deterministic): shared MLP trunk; **actor** over {short, flat, long} trained by all-action (expected) policy gradient on reward = position × return − costs − risk penalty, with entropy bonus; **critic** value head; **forecast** head trained on log score for calibrated P(up) | Unit tests: learns a planted signal, stays near base rate on noise, gradients checked numerically |
| R2 | **Continual walk-forward training**: at every cutoff retrain on resolved outcomes only (warm start, weight decay, time-ordered early stopping), inputs = price features + fundamentals + all LLM arms' outputs | Point-in-time test: an outcome resolving after the cutoff can't enter training |
| R3 | **Guard**: adopt the updated policy only if it beats the base rate on a recent held-out slice; otherwise fall back | Adoption log in results; dashboard Self-improvement tab shows it |
| R4 | Scores: `rl_forecast` (Brier/accuracy like every arm), `rl_trader` (weekly P&L and Sharpe **after costs**) | In v5 results |

**Honest framing (written before any result):** this is a one-step contextual bandit.
Positions don't change future prices, and every action's reward is known once the
outcome resolves, so the policy gradient has no sampling noise. Its forecast head is
equivalent to supervised learning with a proper scoring rule. RL adds the trading
objective (costs, risk), which is not a likelihood. Training can only find signal that
exists in the inputs. If the inputs have none, the guard should keep it at the base rate.

**Success criteria (unchanged from the original plan, plus R):**
1. Rank IC's week-clustered 95% CI above 0 after warm-up, **and**
2. beats the surprise baseline on the same predictions (paired CI), **and**
3. leak probe under 20%.
For Phase R: `rl_forecast` beats `always_up` on Brier with a week-clustered CI excluding 0,
and `rl_trader` has positive after-cost Sharpe with a CI excluding 0.
Anything else is reported as "no edge".

## Phase D — Engineering hygiene. ✅ Done 2026-09-14

| # | Task | Result |
|---|---|---|
| D1 | CI on every push | ✅ CI had failed on **every** push since the repo was created. Fixed (missing dashboard/type-stub dependencies, a test that assumed bubblewrap, coverage gaps). First green run: `4678ab7` |
| D2 | mypy | ✅ 0 errors across all 85 modules (was 31); strict mode on 60 modules in CI |
| D3 | README "Verified state" | ✅ Rewritten with re-verified claims only (final pass) |
| D4 | Dashboard | ✅ Receipts, forward-test ledger, fundamentals/RL arms, rank IC table, RL training section |
| D5 | Coverage | ✅ 92% on the safety-critical modules (floor 85%). The agent's in-jail tasks are now tested in-process too |

## Phase E — Decision gate (~1 h)

- **If Phase C meets all three criteria:** don't trust it yet. Add it to the
  forward test (B3) and wait 8–12 weeks. Only a forward-test pass counts.
- **If it doesn't:** publish the negative result clearly, and stop spending
  GPU time on price-direction prediction. Refocus the project on what LLMs
  demonstrably do well here, using the same point-in-time pipeline: a filings
  summarizer and research assistant that doesn't need to predict prices.

---

## Phase F — Research-driven optimization (proposed 2026-09-16, not started)

From a review of public repos, Hugging Face models, and recent papers: [`RESEARCH_OPTIMIZATION.md`](RESEARCH_OPTIMIZATION.md).
Main finding in our own data: 19,319 cached LLM answers use only 18 distinct `p_up` values (42% are 0.52), which
limits rank IC. Planned fixes, each in a new config frozen before its first run: a log-prob `llm_lp` arm, a Lookahead
Propensity leak test, Kronos (pre-training ends June 2024, clean for our window) and classical-anomaly baselines,
a warm-started stacker, and Deflated Sharpe in reporting. v5/v6 finish first, unchanged.

## Order and timeline (revised 2026-09-14)

| Step | Work | GPU |
|---|---|---|
| A, W, B | ✅ done | — |
| Bug/optimization pass + full test | 1 h | short |
| C0–C6 + R1–R3 | ~1 day of work | pilot runs |
| C3 leak probe | 15 min | ~10 min |
| C7 v5 long run (100 stocks × 64 weeks, 3 LLM arms + RL) | 30 min to set up | ~3–4 h |
| C7 v6 (20-day) | — | ~1 h |
| Two debugging/optimization passes, final verification, executive summary | 2 h | reproduce checks |
| Forward test | automatic | ~10 min/week, 8–12 weeks |
| E decision | after v5/v6 (first read), after the forward test (final) | — |

## Decisions (answered 2026-09-13)

1. **SEC contact:** a User-Agent with name and email is set; see the EDGAR connector (C1).
2. **Scheduled job:** the PC can't be guaranteed on Monday evenings. So B3 must
   run whenever the PC is next on (e.g. a systemd user timer with
   `Persistent=true`). A week whose prediction couldn't be made before its
   cutoff's next market open is logged as **missed**, never backfilled.
3. **Pushes:** approved at the end of each phase.
