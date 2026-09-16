# Walk-forward LLM simulation on a single RTX 5070

This fork (`airp-local-5070`) adds an honest, leakage-proof accuracy test of a
local LLM forecasting agent on **real** prices, running entirely on one
12 GB RTX 5070. The original `airp-local` sandbox only exercised the plumbing
against a synthetic sine-wave price series.

```bash
cd backend
pip install -e '.[dev]' && pip install yfinance
python scripts/fetch_prices.py --end 2026-09-12          # real adjusted closes -> data/prices.csv
ollama pull qwen3:8b
python -m app.sandbox.walkforward --probe-memorization  # where does the model's knowledge stop?
python -m app.sandbox.walkforward --config configs/v2.toml   # a frozen experiment: seconds from the committed cache, ~20 min uncached
python scripts/reproduce.py                             # re-check every published run (seconds, no GPU)
pytest tests/test_walkforward_sandbox.py tests/test_jail_hardening.py   # leakage and jail guarantees
```

## Reproducibility and receipts

- **Frozen experiments.** Each published run has a config in `backend/configs/`
  (`v2.toml`, `v3_excess.toml`, `v4_14b.toml`). A tag always means one
  experiment: re-running a tag with different parameters is refused unless you
  pass `--force`.
- **Receipts in every report.** `config`, `config_hash`, git commit and whether
  source files had uncommitted changes, the sha256 of `prices.csv`, the Ollama
  model digest, Python/platform/GPU, and the jail limits. The dashboard shows
  these under "Receipts".
- **Pinned data.** `configs/data.lock.json` records the price file's checksum.
  Yahoo re-adjusts old prices after dividends, so a fresh download can differ.
  `reproduce.py` reports a mismatch (exit 3) instead of silently comparing
  against different data.
- **Exact reproduction without a GPU.** The LLM response cache
  (`results/llm_cache_*.jsonl`, ~1.3 MB) is committed. `scripts/reproduce.py`
  re-runs every frozen config and compares every score and every individual
  prediction with the published files. As of commit `ec59aff`, all three
  published runs reproduce **identically** with 0 new LLM calls. A negative
  control (one changed setting) is correctly reported as different.

## What the research says (and what we did about it)

| Finding | Source | Design response |
|---|---|---|
| LLMs memorize historical market data; accuracy measured inside the training window is inflated and collapses after the cutoff. Telling the model to "ignore what you know" does not work. | Lopez-Lira, Tang & Zhu, *The Memorization Problem* ([arXiv:2504.14765](https://arxiv.org/abs/2504.14765)) | Test window starts **after** the model's training cutoff, measured, not assumed (`--probe-memorization`). |
| Lookahead propensity is positive in-sample and drops to ~0 right after the cutoff; predictive power concentrates on high-memorization observations. | Gao, Jiang & Yan, *Detecting Lookahead Bias in LLM Forecasts* ([arXiv:2512.23847](https://arxiv.org/abs/2512.23847)) | Same: the price-recall probe is a cheap version of their date-recall query. |
| Removing company identifiers de-biases backtests; the "distraction effect" of knowing the company is even larger than lookahead. | Glasserman & Lin ([arXiv:2309.17322](https://arxiv.org/abs/2309.17322)) | Agent sees no tickers and no dates: rebased-to-100 series labelled "asset" and "market". |
| Memorization-contaminated signals can be filtered via membership inference / cross-model disagreement. | MemGuard-Alpha ([arXiv:2603.26797](https://arxiv.org/abs/2603.26797)) | Noted as a future option; unnecessary here because inputs are anonymized numbers only. |
| Layered memory + reflection lets a trading agent improve from its own outcomes. | FinMem ([arXiv:2311.13743](https://arxiv.org/abs/2311.13743)); TradingAgents ([arXiv:2412.20138](https://arxiv.org/abs/2412.20138)) | `llm_selfimprove` arm: own track record, self-written lessons, and a learned stacker. All **point-in-time**: an outcome joins memory only after its resolution date. |

Similar open-source projects: **TradingAgents** (multi-agent analyst/debate
pipeline), **FinMem** (layered memory + reflection), and **FinGPT** (fine-tuned
financial LLMs). The ideas we borrowed are memory/reflection. What this repo adds
is the evaluation discipline the memorization papers call for, enforced in code:
process isolation, point-in-time memory, anonymization, a measured post-cutoff
window, and non-LLM baselines scored on the identical grid.

## The sandbox: layers, each tested

1. **Data** — `PriceTable.view(cutoff)` returns copies of rows `<= cutoff` only;
   a view carrying a later row cannot be constructed. The grading lookup
   `PriceTable.outcome()` raises `LookaheadViolation` inside a sandbox scope.
2. **Process jail** — the agent (`agent_worker.py`, stdlib only) runs under
   bubblewrap with no network namespace (not even localhost), a cleared
   environment, and read-only access to only the OS runtime, Python, and its own
   source file. It cannot open `data/prices.csv`, the repo, or `$HOME`. The
   orchestrator relays its LLM prompts to Ollama. Every run probes this live
   and **refuses to start** if the probe fails; the result is saved in the
   results JSON. Resource limits (via `prlimit`: 2 GB memory, CPU time, 64 open
   files, 1 MB max file size), send and response timeouts, maximum message size, a cap
   on LLM calls per message, and JSON validation mean a misbehaving agent is
   killed with `JailError` and the run aborts before writing results
   (`tests/test_jail_hardening.py` runs hostile workers both jailed and unjailed).
3. **Memory** — resolved records enter memory at cutoff *c* only if the
   resolution date is `<= c`, re-checked by `enforce_point_in_time` inside
   `sandbox_scope(c)`.
4. **Model knowledge** — anonymized inputs + post-cutoff window (above).

Also fixed in the existing code: `DataConnector.fetch()` served cache hits
without any point-in-time check, so a record cached by a live call could be
returned inside a sandbox. Cache keys are now namespaced by the active
cutoff (regression test: `test_connector_cache_cannot_bypass_sandbox`).

## Model choice for a 5070 (12 GB)

The desktop already holds ~3.9 GB of VRAM, leaving ~8 GB. `qwen3:8b` (Q4_K_M,
5.6 GB loaded) runs 100% on GPU with 4 parallel requests at ~2–2.6 calls/s with
thinking disabled. `qwen3:14b` (9.6 GB loaded) also fit 100% on GPU in our run, once the desktop
had released some VRAM, but at ~1 call/s. See `v4_14b` below for whether it was worth it.

## Memorization probe (why the window starts 2025-06-02)

`python -m app.sandbox.walkforward --probe-memorization --model <model>` asks
the model, with no data, for the closing price of 5 stocks (AAPL, MSFT, NVDA,
KO, JPM) on the 15th of each month. The results file records, per month, the
median absolute percentage error **over answers it could parse**, and
separately the share of refusals or unparseable replies.

The first version counted an unparseable reply as 100% error, which reads as
"not memorized". That biases the check toward the reassuring answer. qwen3:14b
left up to 60% of questions unanswered in some months, so the old scoring
showed a misleading 100% for 2025-09. The scores were recomputed from the
cached answers (no new model calls).

| model | Jan 2024 – Apr 2025 | May – Dec 2025 | 2026 |
|---|---|---|---|
| qwen3:8b | 18.8% | 30.5% | 22.8% |
| qwen3:14b | 15.6% | 31.6% | 35.3% |

(medians of the monthly medians; lower = better recall)

Both models recall prices poorly throughout, far from memorization, and
recall gets clearly worse from about May 2025. So the 2025-06-02 window start
is defensible for **both** models. Two caveats: only 5 stocks per month make
each monthly number noisy (2025-09 for 14B is 19% on 2 answers), and poor
price recall doesn't rule out the model knowing broad market events. Combined
with anonymization (no tickers, no dates, prices rebased to 100), recall of
specific outcomes is still not a credible explanation for any score below.

## Results

All scores are after warm-up, on real daily closes of 20 large-cap US stocks.
Each prediction is P(5-trading-day return > 0). Arms:

- `always_up`: always predicts up.
- `base_rate`: rolling up-rate from resolved outcomes only.
- `momentum_20d`, `reversal_5d`: rule baselines.
- `feat_logit`: logistic regression on the same features the LLM sees, no LLM.
- `llm_plain`: jailed LLM.
- `llm_selfimprove`: jailed LLM plus its own track record, reflection lessons,
  and a holdout-guarded stacker.
- `selector`: picks the arm with the best rolling Brier score from resolved outcomes.

A Brier score of 0.25 is a coin flip.

### `v2` — qwen3:8b, target=abs, 2025-06-02..2026-09-02, 64 cutoffs x 20 tickers

Scored after warm-up (from 2025-08-27), n=1040; 1 standard error of accuracy is about ±1.6 pts. Jail probe passed: True (start), True (end). LLM calls: 0 new + 2575 cached.

| arm | accuracy | % 'up' calls | Brier (lower=better) | long/short weekly ret | L/S Sharpe |
|---|---|---|---|---|---|
| always_up | 52.1% | 100% | **0.2497** | +0.59% | 2.12 |
| base_rate | 52.1% | 100% | 0.2530 | +0.59% | 2.12 |
| momentum_20d | 50.3% | 56% | 0.2500 | +0.09% | 0.41 |
| reversal_5d | 48.2% | 47% | 0.2505 | -0.19% | -0.78 |
| feat_logit | 52.1% | 100% | 0.2544 | +0.59% | 2.12 |
| llm_plain | 50.7% | 93% | 0.2505 | +0.35% | 1.48 |
| llm_selfimprove | 47.1% | 73% | 0.2604 | +0.02% | 0.08 |
| selector | 50.0% | 79% | 0.2509 | +0.19% | 0.99 |

Stacker decisions: {'not_enough_data': 10, 'adopted': 40, 'rejected': 14}. Selector choices: {'base_rate': 5, 'llm_plain': 40, 'llm_selfimprove': 1, 'reversal_5d': 3, 'momentum_20d': 13, 'feat_logit': 2}.


### `v3_excess` — qwen3:8b, target=excess, 2025-06-02..2026-09-02, 64 cutoffs x 20 tickers

Scored after warm-up (from 2025-08-27), n=1040; 1 standard error of accuracy is about ±1.6 pts. Jail probe passed: True (start), True (end). LLM calls: 1395 new + 1180 cached.

| arm | accuracy | % 'up' calls | Brier (lower=better) | long/short weekly ret | L/S Sharpe |
|---|---|---|---|---|---|
| always_up | 48.8% | 100% | 0.2503 | +0.25% | 1.79 |
| base_rate | 49.3% | 21% | 0.2504 | -0.11% | -0.79 |
| momentum_20d | 49.4% | 56% | 0.2502 | -0.02% | -0.10 |
| reversal_5d | 51.0% | 47% | **0.2499** | -0.05% | -0.26 |
| feat_logit | 48.2% | 24% | 0.2514 | -0.24% | -1.30 |
| llm_plain | 47.2% | 96% | 0.2517 | +0.08% | 0.49 |
| llm_selfimprove | 48.3% | 27% | 0.2513 | -0.20% | -1.15 |
| selector | 47.5% | 57% | 0.2512 | -0.23% | -1.27 |

Stacker decisions: {'not_enough_data': 10, 'adopted': 25, 'rejected': 29}. Selector choices: {'base_rate': 5, 'llm_selfimprove': 2, 'momentum_20d': 20, 'feat_logit': 2, 'llm_plain': 9, 'reversal_5d': 26}.


### `v4_14b` — qwen3:14b, target=abs, 2025-06-02..2026-08-26, 32 cutoffs x 20 tickers

Scored after warm-up (from 2025-08-27), n=520; 1 standard error of accuracy is about ±2.2 pts. Jail probe passed: True (start), True (end). LLM calls: 1295 new + 0 cached.

| arm | accuracy | % 'up' calls | Brier (lower=better) | long/short weekly ret | L/S Sharpe |
|---|---|---|---|---|---|
| always_up | 55.0% | 100% | **0.2491** | +1.00% | 3.35 |
| base_rate | 55.0% | 100% | 0.2493 | +1.00% | 3.35 |
| momentum_20d | 51.3% | 57% | 0.2498 | +0.11% | 0.42 |
| reversal_5d | 50.6% | 49% | 0.2500 | +0.04% | 0.13 |
| feat_logit | 55.0% | 100% | 0.2497 | +1.04% | 3.21 |
| llm_plain | 52.7% | 82% | 0.2499 | +0.58% | 2.72 |
| llm_selfimprove | 51.1% | 89% | 0.2521 | +0.37% | 1.60 |
| selector | 48.3% | 86% | 0.2554 | +0.01% | 0.03 |

Stacker decisions: {'not_enough_data': 10, 'rejected': 12, 'adopted': 10}. Selector choices: {'base_rate': 5, 'llm_selfimprove': 7, 'llm_plain': 2, 'feat_logit': 14, 'momentum_20d': 4}.


`v1` (the first qwen3:8b run, with the unguarded stacker) is kept in
`results/walkforward_v1.json`. After warm-up: `llm_plain` 50.7%,
`llm_selfimprove` 49.8%, `always_up` 52.1%. The weak L2 let the stacker
overfit, which is why v2 added real L2 and the holdout guard.

### `v2b_pit_universe` (survivorship-free) — qwen3:8b, target=abs, 2025-06-02..2026-09-02, 64 cutoffs x 20 tickers

Scored after warm-up (from 2025-08-27), n=1040; 1 standard error of accuracy is about ±1.6 pts. Jail probe passed: True (start), True (end). LLM calls: 129 new + 2446 cached.

| arm | accuracy | % 'up' calls | Brier (lower=better) | long/short weekly ret | L/S Sharpe |
|---|---|---|---|---|---|
| always_up | 51.9% | 100% | **0.2497** | +0.63% | 1.46 |
| base_rate | 51.9% | 100% | 0.2540 | +0.63% | 1.46 |
| momentum_20d | 49.8% | 52% | 0.2501 | +0.10% | 0.38 |
| reversal_5d | 50.4% | 47% | 0.2500 | +0.10% | 0.35 |
| feat_logit | 51.1% | 99% | 0.2553 | +0.46% | 1.09 |
| llm_plain | 49.7% | 91% | 0.2512 | +0.15% | 0.44 |
| llm_selfimprove | 50.6% | 97% | 0.2535 | +0.39% | 1.00 |
| selector | 49.3% | 69% | 0.2507 | -0.02% | -0.06 |

Stacker decisions: {'not_enough_data': 10, 'rejected': 31, 'adopted': 23}. Selector choices: {'base_rate': 5, 'llm_selfimprove': 25, 'llm_plain': 4, 'momentum_20d': 21, 'reversal_5d': 8, 'feat_logit': 1}.


The universe is the S&P 500 as it stood on 2025-06-02 (a dated Wikipedia
revision), ranked by prior-year dollar volume, top 20, one share class per
company (`scripts/build_universe.py`, `data/universe_2025-06-02.meta.json`).
v2 used today's well-known names, which quietly favours stocks that did well.
v2b's settings are v2's, frozen before its first run. Week-clustered Brier gap
vs `always_up` (negative = better): `llm_plain` +0.0014 [−0.0005, +0.0033],
`llm_selfimprove` +0.0038 [−0.0021, +0.0098], `feat_logit` +0.0056
[−0.0028, +0.0140], `reversal_5d` +0.0003 [−0.0009, +0.0016]. **Same
conclusion as v2: nothing beats always-up.**

### What the numbers say

**Significance, done properly.** Stocks in the same week move together, so
the ±SE above (which treats every prediction as independent) is too narrow.
The dashboard's Brier-gap test resamples whole weeks (bootstrap, 2,000 draws)
and compares each arm with `always_up` on identical predictions:

| run | arm | Brier gap vs always_up | 95% CI | verdict |
|---|---|---|---|---|
| v2 | llm_plain | +0.0009 | [−0.0010, +0.0027] | no clear difference |
| v2 | llm_selfimprove | +0.0107 | [+0.0035, +0.0183] | **significantly worse** |
| v3_excess | llm_plain | +0.0013 | [+0.0004, +0.0023] | **significantly worse** |
| v3_excess | llm_selfimprove | +0.0009 | [−0.0010, +0.0030] | no clear difference |
| v4_14b | llm_plain | +0.0008 | [−0.0025, +0.0041] | no clear difference |
| v4_14b | llm_selfimprove | +0.0030 | [−0.0029, +0.0086] | no clear difference |

No arm in any run is significantly *better* than `always_up`.

- **No LLM arm beat `always_up` on accuracy or Brier in any run.** With a 1-SE
  band of ±1.6 pts (n=1040) or ±2.2 pts (n=520), every arm is within noise of a
  coin flip or of the up-drift base rate. The LLM mostly learned to say "up" in
  a rising market (82–96% up calls), and was less calibrated than the trivial
  baseline doing so.
- **Self-improvement did not help.** Reflection lessons and the track record
  did not raise accuracy. The guarded stacker was adopted only when a holdout
  said it helped, yet `llm_selfimprove` still scored at or below `llm_plain`
  on Brier in v2 and v4. That is what you expect when there is no stable
  signal to learn: the feedback loop fits noise. The guard limits the damage
  but cannot create a signal.
- **Removing market drift (`v3_excess`) leaves nothing.** Predicting whether a
  stock beats SPY over 5 days, every arm sits at 47–51%, with Brier ≈ 0.25.
- **A bigger model (`v4_14b`) did not change the conclusion.** qwen3:14b was
  somewhat more balanced than 8B (82% vs 93–96% up calls) but still trailed
  `always_up` (52.7% vs 55.0%, Brier 0.2499 vs 0.2491). It ran about 2.5× slower.
  qwen3:8b stays the default.
- **The long/short Sharpe figures are not tradeable evidence.** They are
  computed on the same small, rising-market sample without costs. `always_up`
  "wins" there only because the market went up.

### Why no custom fine-tune

A LoRA fine-tune on this task would train on the same ~1,300 labelled weekly
outcomes that `feat_logit` already fits, and `feat_logit` shows those features
carry no out-of-sample edge. A fine-tuned model would memorize the backtest
period, which is exactly the lookahead contamination this repo exists to rule
out. It would also need VRAM this card does not have with the desktop loaded. The
effort is better spent on better inputs (below), not on the model.

### Limitations (read before citing)

- The v1 and v2 designs were tuned while looking at results from the same
  window. v3 and v4 changed only the target and the model. A clean claim of "no
  edge" is conservative in this direction: tuning on the test set can only
  flatter the LLM arms, and they still lost.
- 20 large caps, one ~15-month mostly rising period, a 5-day horizon, and price
  and volume features only. These results do not say LLMs are useless for
  research. They say this setup has no short-horizon, price-only edge.
- Prices come from yfinance and are not redistributed (`backend/data/*.csv` is
  gitignored). Re-run `scripts/fetch_prices.py` to reproduce. The LLM response
  cache is also gitignored, so re-runs call Ollama again.

### Where an edge might plausibly come from next

1. Point-in-time **fundamentals, filings, and news** fed through the same jail
   (each document timestamped and filtered by the cutoff). The literature's
   positive results come from text, not price series.
2. **Longer horizons** (1–3 months), where drift and fundamentals dominate
   day-to-day noise.
3. **Ranking within the universe** instead of absolute up/down, scored with
   rank IC, which removes the market-drift shortcut the LLM keeps taking.
4. A test window that starts after the model's cutoff, re-measured with the
   probe for every new model.

## Forward test (pre-registered, live)

Backtests can always be doubted. The forward test can't be tuned after the
fact. Every week, after the last close and **before the next market open**,
`python -m app.forward.run` records for each stock of the frozen universe:

- `live_plain`: the backtest's price-only agent, unchanged;
- `live_web`: the web research agent (news, articles, SEC filings, prices
  through guarded tools; `docs/LIVE_TOOLS.md`).

Rules, all enforced in code (`app/forward`, `tests/test_forward.py`):
- Settings and success criteria are frozen in `configs/forward_v1.toml`
  before the first decision.
- Records go into an append-only, hash-chained ledger
  (`results/forward/forward_v1.jsonl`). Editing, deleting or reordering any
  past line breaks verification. Pushing the ledger to GitHub adds an
  independent timestamp.
- A week not decided before its deadline (09:30 ET on the next weekday) is
  logged as **missed** and never backfilled. A decision that finishes after
  the deadline is logged as late and excluded from scoring.
- Outcomes are appended only after the 5-trading-day horizon has closed.
- `scripts/forward_timer.sh install` runs the idempotent job hourly and after
  boot (systemd user timer, `Persistent=true`), since the PC isn't always on.

**Success criterion (pre-registered):** `live_web` beats both `live_plain` and
`always_up` on Brier, with a week-clustered 95% CI excluding 0, after at least
8 scored weeks. The first decision (week ending 2026-09-11) was logged on
time at 07:30 UTC on 2026-09-14. The dashboard's Live tab shows the verified
ledger and scoreboard.

## Phase C: SEC fundamentals, point-in-time

**Universe (C0).** The 100 S&P 500 members with the highest prior-year dollar
volume on 2025-06-02, from the same dated Wikipedia revision as v2b
(`data/universe_2025-06-02_top100.csv`). 15 members couldn't be ranked because
Yahoo no longer has their data (mostly companies acquired since, e.g. HES,
JNPR, WBA); they're listed in the meta file. That is residual survivorship
bias, small at this size.

**Data (C1–C2, `app/data_ingestion/edgar.py`).** SEC submissions (the filing
index, including paged older files) and XBRL values for diluted EPS and
revenue, for all 100 companies, stored as `data/edgar/fundamentals.json`
(public-domain SEC data, committed). Requests carry the contact User-Agent
from `backend/.env` and are paced under SEC's limit. Two data quirks are
handled:
- The per-concept API returned empty values for some companies (e.g. KO),
  so the downloader falls back to the company-facts file.
- Multi-class companies (V, BRK-B) don't tag a single EPS, so net income is
  used instead. The surprise measure is scale-free, so this is sound.

Point-in-time rules (tested in `tests/test_edgar_pit.py`):
- a filing or value counts only if **filed on a date before** the cutoff (a
  filing on the cutoff day after the close would otherwise leak);
- restated numbers count only from the restating filing's own date;
- Q4 is derived as FY − Q1..Q3 once the 10-K is filed;
- every date used is re-checked against the sandbox clock, so a bug aborts
  the run instead of leaking.

The features per stock and week are:
- SUE (standardized unexpected earnings: year-over-year EPS change divided by
  the spread of the prior 8 changes) for the last four quarters;
- EPS and revenue growth versus the same quarter a year earlier;
- days since the last quarterly report and the last earnings release (8-K
  Item 2.02);
- the number of 8-Ks in the last 30 days;
- whether an earnings release is expected within the horizon, based on last
  year's reporting calendar.

**What the agent sees.** A numbers-only sentence: no names, no dates, no text
from filings (`digest_text`).

**Leak probe (C3).** For 100 stocks × 3 random weeks, the model was asked to
name the company, once from anonymized prices and once from prices plus the
digest. It identified the company 1.3% and 1.0% of the time respectively. It
answered "T" (AT&T) or "BMY" for most stocks regardless of the data, which is
chance level. Threshold 20%: **passed** (`results/leak_probe_qwen3_8b.json`).

**New arms (C4–C5).**
- `llm_fund`: the jailed agent with the digest.
- `llm_selfimprove`: now also sees the digest.
- `sue_rule`: multi-quarter surprise sign, weights 0.4/0.3/0.2/0.1, no fitting.
- `feat_fund_logit`: walk-forward logistic regression on price + fundamental
  features.

**New scores (C6, `app/sandbox/scoring.py`).** Cross-sectional rank IC per
week (Spearman between forecast and realized return) and the top-minus-bottom
fifth return, both with confidence intervals that resample whole weeks.
Tested against SciPy and on synthetic data with a known IC.

## Phase R: deep reinforcement learning on top of the jailed LLM

`app/learning/rl_agent.py` (numpy, deterministic). Each week, for each stock,
the state has 26 inputs:
- the 9 price features;
- the 11 fundamentals features;
- the logits of the three LLM arms' probabilities;
- within-week percentile ranks of the 5- and 20-day returns and of the
  fundamentals LLM's forecast.

The policy picks {short, flat, long}. Its reward is position × standardized
return − 5 bp costs − a risk penalty. The network is a shared 32×32 tanh trunk
with an actor (softmax over the three positions), a critic (value of the
current policy), and a forecast head for P(up) trained on the log score, so
its probabilities stay calibrated.

Every action's reward is known once a week resolves, so the policy gradient is
computed exactly over all actions. Gradients are verified numerically in
`tests/test_rl_agent.py`.

**Continual training.** At every cutoff:
1. **Data:** it retrains on outcomes resolved by that date, re-checked
   against the sandbox clock.
2. **Starting point:** it warm-starts from last week's weights.
3. **Regularization:** weight decay, plus early stopping on the most recent
   25% of resolved weeks.
4. **Guard:** the forecast head is used only if it beats the base rate on
   those held-out weeks, and the trader only if it beats staying flat.
   Otherwise it falls back to the base rate or to flat.

The weekly decisions are recorded in the results (`rl_log`) and shown on the
dashboard.

**Honest framing, written before any result.** This is a one-step contextual
bandit: positions don't move future prices. With a proper scoring rule, the
forecast head is equivalent to supervised learning. RL adds the trading
objective with costs and risk. Training can only find signal that exists in
the inputs, and the guard's job is to keep it at the base rate when there is
none. The LLM's own weights are not trained: retraining them on 2025–26
outcomes would teach the model the test window, the leak this whole setup
exists to prevent.

## Phase F: log-prob scores, stronger baselines, sharper leak test (methods written before any v7 result)

Background and sources: [`RESEARCH_OPTIMIZATION.md`](RESEARCH_OPTIMIZATION.md). Criteria: `EXECUTION_PLAN.md`, Phase F.

**Why.** The verbalized arms answer with a handful of round numbers: 19,319 cached answers use 18 distinct
values, 42% of them 0.52. Ranking 100 stocks with that many ties is mostly tie-breaking.

**Log-prob arms (`llm_lp`, `llm_fund_lp`).** Same anonymized inputs as `llm_plain` and `llm_fund`, but the system
prompt ends "Answer with exactly one word: UP or DOWN." Ollama returns the top-20 token log-probabilities of that
single generated token, and P(up) = P(UP) / (P(UP) + P(DOWN)), summing case and punctuation variants. qwen3 emits
`UP` and `DOWN` as single tokens (checked on the real model). If neither word appears, the answer is 0.5 and
counted in `updown_no_mass`. The jail relays only the mode flag: the agent still never sees the network or the
data. These probabilities are sharp (the pilot gave P(DOWN) = 0.98), so they are for ranking. Brier results are
reported but expected to be poor without calibration.

**Anomaly baselines.** Computed point-in-time from the same price view: 12−1 momentum, 1-month reversal,
52-week-high proximity, 60-day idiosyncratic volatility, plus the SEC surprise composite. `anomaly_rank` averages
signed within-week percentile ranks with the published signs, with a fixed mapping to P(up) of 0.45–0.55 and
nothing fitted. `anomaly_logit` is a walk-forward logistic on those features plus the price features. A test
rewrites all prices after the cutoff and checks that no feature changes.

**Kronos.** Kronos-small (24.7M, MIT; code commit `67b630e`, weights revision `901c26c`, tokenizer `0e01173`) is
pre-trained on candlesticks up to June 2024, before this window. For each cutoff and stock it sees the last 256
daily OHLCV bars dated on or before the cutoff. Future timestamps are generated business days, not read from
data. It draws 24 sampled 5-day paths, and P(up) = Φ(mean/sd of the sampled returns), shrunk halfway to 0.5.
Forecasts are made once, before the v7 run, into `results/kronos_v7_phase_f.jsonl`. The run refuses them unless
the OHLCV sha256, parameters, cutoffs, horizon and file hash all match.

**Leak test: Lookahead Propensity** (Gao et al., arXiv 2512.23847). With real tickers and dates, the model is asked
whether a stock closed higher over a given 5-day span: UP, DOWN, or UNKNOWN. LAP is the probability it puts on a
direction. Monthly LAP over 2024–26 should fall after the training cutoff. On v7's own grid, `scripts/lap_test.py`
fits up ~ s + LAP + s×LAP (s = p − 0.5) with a week-clustered bootstrap. A positive interaction means forecasts are
better exactly where the model remembers more: the leak signature. A planted-leak test shows the procedure catches
one.

**Stacker.** The self-improving arm's stacker and `feat_logit` use an exact Newton solver for the same penalized
objective. That removes the late-run slowdown and the early stopping that made the old fits depend on the step
count.

**RL.** The agent's state adds `llm_fund_lp`, the anomaly composite and Kronos (raw values and within-week ranks).
Nothing else about training or its guard changes.

**Multiple testing.** Every long/short arm gets a Probabilistic Sharpe Ratio and a Deflated Sharpe Ratio (Bailey &
López de Prado). The number of trials is every arm of every published walk-forward run, and the luck benchmark
comes from the spread of Sharpes across arms. The RL trader must reach DSR > 0.95.

**Serving.** v7 runs on the tuned Ollama user service (:11435, 4 parallel slots; 1.6× measured). Parallel batching
makes regenerated answers non-bit-identical, but every answer is cached, so v7 reproduces exactly from its cache.
Its verbalized arms reuse v5's cached answers for identical prompts.

