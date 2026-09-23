# Research-driven optimization plan (2026-09-16)

Written from a review of public GitHub repos, Hugging Face models, and recent papers, checked against
this repo's code and its own saved data. **Nothing here has been run yet.** Every item that changes
predictions becomes a *new* config that is frozen before its first run. `v5_fund_top100` and
`v6_fund_rank20d` stay as they are.

## 0. What the outside evidence says to expect

- **Long, wide backtests erase LLM "alpha."** FINSABER (KDD 2026) re-tested published LLM trading
  strategies over 20 years and 100+ stocks. The claimed advantages shrink sharply, and LLM strategies
  trail passive benchmarks. They are too cautious in bull markets and too aggressive in bear markets.
  That matches our result so far: in a rising 2025–26 window, nothing beat "always up."
  ([repo](https://github.com/waylonli/FINSABER), [paper](https://arxiv.org/abs/2505.07078))
- **Time-series foundation models barely beat a random walk on stock returns.** A 2026 comparison
  (Chronos, Chronos-2, TimesFM-2.5, Moirai-2.0, TimeGPT; 5 US large caps; 20-day horizon) found skill
  scores around 10⁻³. The improvement was statistically significant in only 2 of 10 tasks.
  ([arXiv 2606.27100](https://arxiv.org/abs/2606.27100))
- **What a good cross-sectional score looks like:** Qlib's Alpha158 features with LightGBM reach a rank
  IC of about **0.048** (CSI300, 20 seeds). That is a well-tuned tree model with 158 features. A
  weekly rank IC of 0.02–0.05 with a CI above 0 would be a real result here. Direction accuracy far
  above 55% would not be believable.
  ([qlib benchmarks](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md))

Implication: optimize for **rank IC on a wide universe, measured honestly.** Headline accuracy is the
wrong target. Treat the strong non-LLM baselines below as the bar the LLM has to clear.

## 1. Measured weakness in our own data: the LLM's probabilities barely vary

The committed cache (`backend/results/llm_cache_qwen3_8b.jsonl`) holds **19,319 answers that use only
18 distinct `p_up` values**. **42% are exactly 0.52** and 23% are 0.54. The prompt tells the model to
stay between 0.40 and 0.60, and at temperature 0 it settles on a few round numbers. With that many
ties, rank IC is mostly noise from tie-breaking, and the stacker and RL agent get almost no signal
from `llm_logit`. This is the single largest fixable limit on Phase C.

## 2. Priority 1: cheap, high value, low leak risk

### 2.1 Continuous LLM scores from token log-probabilities
Ollama 0.34 (installed) returns `logprobs` and `top_logprobs` (0–20) on `/api/chat`
([API types](https://pkg.go.dev/github.com/ollama/ollama/api)).
- New arm `llm_lp`: same anonymized prompt, but the reply is one word, `UP` or `DOWN`, with
  `logprobs: true, top_logprobs: 5, num_predict: 2`. Score = log P(UP) − log P(DOWN), renormalized over
  the two tokens. The result is continuous (no ties) and about 100x fewer generated tokens than the JSON
  answer (~300-token budget today), so it is also much faster.
- Keep the verbalized arm. The literature is split on which is better calibrated
  ([arXiv 2410.06707](https://arxiv.org/pdf/2410.06707), [2605.27752](https://arxiv.org/pdf/2605.27752)),
  so run both and let the week-clustered CIs decide. For **ranking**, a continuous score is strictly
  more usable either way.
- Code: `OllamaLLM` in `backend/app/sandbox/walkforward.py:71` (add the fields, add `lp=1` to the cache
  key the same way `ctx=` was added so existing keys stay valid), `parse_p` in
  `backend/app/sandbox/agent_worker.py:134` (new `parse_lp`). Calibrate with the stacker already in place.
- Tokenizer caveat: check that `UP` and `DOWN` are single tokens in qwen3. If not, compare the first
  subword and record the token ids in provenance.

### 2.2 A stronger leak test: Lookahead Propensity (LAP)
Our memorization probe asks for price levels. The LAP method
([arXiv 2512.23847](https://arxiv.org/abs/2512.23847)) is sharper and needs the same log-prob
machinery:
1. **Date-only query:** give the model only a real ticker and date and ask up, down, or unknown.
   LAP = P(up) + P(down).
2. Check that LAP drops after the model's training cutoff. This also validates our measured cutoff.
3. Regress hit on signal, LAP, and signal × LAP. A significant positive interaction means recall is
   leaking into the "anonymized" forecast.

This upgrades criterion C3 from "can it name the stock" to "does what it remembers change its
forecast." Add it to `--probe-leak`, not as a new criterion for runs that are already frozen.

### 2.3 Ollama server settings (throughput). Measured and applied 2026-09-16
The installed `ollama.service` sets only `PATH`. Ollama's documentation recommends flash attention and a
q8_0 KV cache ([issue #13337](https://github.com/ollama/ollama/issues/13337),
[guide](https://modelpiper.com/blog/ollama-kv-cache-quantization)). **Measured on this machine** (qwen3:8b,
48 walk-forward prompts, 4 concurrent clients, same prompts on every setting):

| Server setting | req/s | answers identical to stock |
|---|---|---|
| stock (flash attention already on by default in 0.34, 1 slot, f16 KV) | 2.7 | reference |
| `FLASH_ATTENTION=1`, 1 slot | ~2.7 (one noisy run at 1.9) | 48/48 |
| + `KV_CACHE_TYPE=q8_0` | 2.7 | 20/48 |
| `NUM_PARALLEL=4` with the default 4k total context | 0.9 (slots starve) | 19/48 |
| **`NUM_PARALLEL=4`, `CONTEXT_LENGTH=16384` (4k per slot)** | **4.3 (1.6×)**, repeated | 19–22/48 (batch-dependent) |

So: q8_0 KV brings no speed here and changes answers, so it is not used. Parallel slots are the real win, but
only with enough total context, and answers then depend on batch composition (max |Δp| 0.04). Applied as a
**user service on :11435** (no sudo; `scripts/ollama/`). **Turned off 2026-09-16 at the owner's request**: v5, v6 and v7 all run on the stock
server (:11434). Every run records the URL, and every answer is cached, so finished runs still reproduce
exactly.

### 2.4 Remove the per-week CPU slowdown. Done: exact Newton solver
`fit_logistic` (`agent_worker.py`) is pure Python with 300 gradient steps. It runs for every stacker fit every
week, and on the host for `feat_logit` over all resolved rows. That is why later v5 weeks took ~120 s even
when every LLM call was cached. Instead of warm starts (which would still stop short of the optimum),
`fit_logistic_newton` solves the **same objective** exactly with Newton's method in ≈10 passes. It is
stdlib-only, so the jail stays numpy-free. A test checks that it matches 5,000 gradient steps and that the
gradient is ~0 at its solution. It is used only by configs with `solver = "newton"` (v7).

### 2.5 Multiple-testing-aware reporting
We report 6–9 arms per run and the RL trader's Sharpe. Add the **Probabilistic and Deflated Sharpe
Ratio** (Bailey and López de Prado; reference implementations
[pypbo](https://github.com/esvhd/pypbo), [deflated-sharpe](https://github.com/Kodezilla0725/deflated-sharpe))
to `scripts/evaluate_criteria.py`, with the number of trials set to the number of arms and configs tried.
Reporting only. It does not change pre-registered pass/fail, but the write-up should show it next to
each Sharpe.

## 3. Priority 2: stronger, leak-checked baselines

### 3.1 Kronos: a candlestick foundation model that is clean for our window
[Kronos](https://github.com/shiyu-coder/Kronos) (MIT, AAAI 2026;
[HF](https://huggingface.co/NeoQuasar/Kronos-small)) is pre-trained on 12B+ OHLCV bars from 45
exchanges. Its **pre-training data ends June 2024** ([arXiv 2508.02739](https://arxiv.org/abs/2508.02739)).
Our test windows start 2025-06-02, so it cannot have seen them.
- Sizes: mini 4.1M, small 24.7M, base 102M (context 512 bars). All fit on the 5070 alongside Ollama.
- Arm `kronos`: sample ~30 paths per stock and cutoff with `predict_batch`. P(up) = share of paths that
  end above the last close. Score = median simulated return, for ranking.
- Weights must be downloaded ahead of time, bound read-only into the jail, and hashed into provenance.
  No network inside the jail.

### 3.2 Chronos-2 / TimesFM-2.5: only with a documented data cutoff
[Chronos-2](https://huggingface.co/amazon/chronos-2) (120M) and
[TimesFM-2.5](https://huggingface.co/google/timesfm-2.5-200m-pytorch) (200M) are strong general
forecasters. Neither documents whether 2025 market data is in its training corpus. The 2026 finance
comparison did not check contamination either. Lower priority: use them only if a data cutoff before
2025-06 can be confirmed from their technical reports. Otherwise say plainly that contamination is
unknown.

### 3.3 Classical anomaly features (well-tested, point-in-time by construction)
[Open Source Asset Pricing](https://github.com/OpenSourceAP/CrossSection) re-derived 319 published
predictors. 98% of the clearly significant ones replicate with t > 1.96. Re-implement the definitions
of a few that need only prices plus our SEC data (the repo is GPL-2, so don't copy code):
1-month reversal, 12−1 momentum, 52-week-high proximity, idiosyncratic volatility, and the existing
SUE. Add them to `RL_STATE_KEYS` and the logistic baselines as arm `anomaly_rank`, a simple average of
ranks. This is the honest non-LLM bar for rank IC.

## 4. Priority 3: actually training the LLM (real "self-improving model")

Today's self-improvement is memory plus lessons plus a stacker and an RL head. The model weights
never change. The best public evidence that outcome RL improves an LLM forecaster:
**"Outcome-based Reinforcement Learning to Predict the Future"**
([arXiv 2505.17989](https://arxiv.org/abs/2505.17989)).
- Setup: 14B base, ReMax (or GRPO with the per-question std scaling removed), reward = −Brier,
  unparseable answer = −1, group size 4, learning rate ~2e-6, one chronological pass, guard rails
  (length cap, gibberish filter, early stop), and an ensemble of 7 runs.
- Result: Brier 0.215 → 0.190, ECE 0.089 → 0.062. But that used ~110k questions and an 8×H100 node.
- **On a 5070:** [Unsloth](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune) GRPO
  with LoRA on Qwen3-4B is feasible. 8B with 4-bit QLoRA is tight. Ollama must be stopped during
  training (shared 12 GB).
- **The leak problem is the whole difficulty.** Training questions must come from before the test
  window, and qwen3 already knows how pre-2025 prices moved. The reward would then teach recall, not
  forecasting. Required mitigations: anonymized inputs (as we already do), drop training examples with
  high LAP (§2.2), and a clean post-cutoff test. Given §0, the expected gain on stock direction is small.
  Pre-register it as an experiment, not as the plan of record.

### Alternative: point-in-time LLMs for decades of clean history
[ChronoGPT](https://huggingface.co/collections/manelalab/chronogpt) publishes models trained only on text
available before fixed yearly cutoffs (1999 … 2024-12-31) ([arXiv 2502.21206](https://arxiv.org/abs/2502.21206));
see also [Look-Ahead-Bench](https://github.com/benstaf/lookaheadbench). Using the model whose cutoff is
just before each test year allows a leak-free walk-forward over **20+ years instead of 64 weeks**. That
is roughly 20x the statistical power, which is what our wide CIs need most. Check each model's size and
license before adopting.

## 5. Recommended order

| # | Change | Kind | Cost | Why first |
|---|---|---|---|---|
| 1 | ✅ v5 and v6 finished 2026-09-22 — both NOT PASSED | finish pre-registered work | done | results were promised before new work |
| 2 | ✅ Measured: tuned Ollama on :11435 is 1.6× faster (q8_0 rejected); available but turned off | infra | done | optional speedup |
| 3 | ✅ Exact Newton stacker (§2.4) | speed | done | removes the late-week slowdown |
| 4 | ✅ run: ties fixed (9 → 3,200 distinct), ranks better than verbalized (+0.019 IC) but still ≈ 0; LAP found no memory to leak | `v7` | done | — |
| 5 | ✅ run: anomaly composite +0.008 [−0.047, +0.063], Kronos −0.018 — neither beat noise | baselines in `v7` | done | honest bar for rank IC |
| 6 | ✅ Deflated Sharpe in the evaluator (§2.5) | reporting | done | multiple arms, multiple configs |
| 7 | ⏸ gated: ChronoGPT long-history walk-forward (needs survivorship-free history back to ~2000) | new study | large | statistical power |
| 8 | ❌ not started: the gate (v7 passes F1) failed | experiment | — | no signal for outcome RL to amplify |

## Operational notes (2026-09-16)
- **Reboot at 15:00** stopped `airp-v5` at week 41/64 and left 755 NUL bytes at the end of the LLM cache, so
  every resume crashed on load. Fixed: the loader skips damaged lines and appends always start a fresh line.
- **GPU fell off the bus at 16:11 (NVIDIA Xid 79)** while a Kronos pilot and Ollama (v5, week 46) used the GPU
  at the same time. The card is unusable until a reboot. Ollama then silently reloaded qwen3:8b **on CPU** and
  answered one v5 request there. Fixes: walk-forward runs check `/api/ps` after every uncached answer and refuse
  (and never cache) answers not computed fully on the GPU. The two answers around the crash were removed from the
  cache. Every GPU entry point now takes one exclusive lock (`app/sandbox/gpu_lock.py`). Xid 79 is usually power
  or PCIe related: if it recurs with a single GPU job, check the PSU and cables, and consider lowering the power
  limit (`sudo nvidia-smi -pl 220`).
- After the reboot, `scripts/phase_f_pipeline.sh` runs the whole remaining GPU chain, one job at a time.
