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
python -m app.sandbox.walkforward --tag v1              # full walk-forward, ~20 min on a 5070
pytest tests/test_walkforward_sandbox.py                # leakage guarantees
```

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
   results JSON.
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

`python -m app.sandbox.walkforward --probe-memorization --model qwen3:8b` asks
the model, with no data, for each stock's month-end closing price. Median
absolute percentage error by month is saved in
`results/memorization_probe_qwen3_8b.json`. Recall is weak throughout (10–25%
error, far worse than true memorization) and gets clearly worse from mid-2025
on (26–32% for 2025-06..2025-11). The test window starts at 2025-06-02, after
that shift. Combined with anonymization (no tickers, no dates, prices rebased
to 100), recall of the specific outcomes is not a credible explanation for any
score below.

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

### What the numbers say

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

