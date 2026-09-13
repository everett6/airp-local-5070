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
thinking disabled. `qwen3:14b` (~9.3 GB) does not fit alongside the desktop and
spills onto the CPU; see results below for whether it was worth it.

RESULTS_PLACEHOLDER
