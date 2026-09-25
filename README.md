# AIRP Local — 5070 edition

> **This repo is `airp-local-5070`, a clone of `airp-local` tuned for a single
> RTX 5070 (12 GB).** It adds a leakage-proof walk-forward test of a local LLM
> forecasting agent on **real** prices: a bubblewrap process jail (no network,
> no data access), point-in-time data and memory, anonymized inputs, a test
> window after the model's measured training cutoff, a self-improving agent
> arm, and non-LLM baselines on the identical grid. It also fixes a cache
> bypass in the original sandbox. **Headline result: across seven frozen runs, up to
> 5,200 post-warm-up predictions each, nothing beat simply predicting "up"** —
> not qwen3 8B or 14B (plain, self-improving, with SEC fundamentals, or scored
> from token log-probabilities), not a deep-RL agent trained on all of them, not
> the Kronos candlestick foundation model, and not a classical anomaly
> composite. Every success criterion was fixed before its run, and all of them
> failed; see
> [`docs/WALKFORWARD_5070.md`](docs/WALKFORWARD_5070.md) for the full results,
> the research behind the design, and how to reproduce.

### Phase F: research-driven optimization (run 2026-09-22 — not passed)

Reading the LLM's probability from token log-probabilities fixed the measured tie problem (9 distinct
probabilities over 6,400 forecasts became 3,200) and ranked better than the verbalized answers
(+0.019 rank IC [+0.0001, +0.0371]) — but both sit at rank IC ≈ 0, and the raw log-prob probabilities are far too
confident to use as probabilities (Brier 0.45 vs 0.25). Kronos and the anomaly composite did no better. The
Lookahead Propensity probe found no memory to leak: for a real ticker and date the model answers "unknown", and
its date-only recall is 47% — chance. Measured on the way: a tuned Ollama configuration is 1.6× faster (off by
default). See [`docs/RESEARCH_OPTIMIZATION.md`](docs/RESEARCH_OPTIMIZATION.md), `docs/WALKFORWARD_5070.md` and
`docs/EXECUTION_PLAN.md` (Phases E and F). `scripts/phase_f_pipeline.sh` re-runs the whole GPU chain, one job at
a time, in the foreground.

### Paper-trading simulator (fake money, free data)

`cd backend && .venv/bin/python scripts/simulate.py v7_phase_f` trades every strategy's weekly picks with $100k of
paper money: fills at the next day's open with slippage, a 20% drawdown halt, rejected bad model outputs, random
tie-breaks, and beta/alpha against SPY. Best result: +62% vs SPY's +18%, but it came from holding 1.6x-beta stocks
in a rising year (alpha +25%, 95% CI −20% to +71%), and the same model came last in the 20-day run. See
[`docs/SIMULATOR.md`](docs/SIMULATOR.md).

### 16 years, and an LLM that researches the internet as of each date

`scripts/build_history.py` builds a point-in-time S&P 500 top-100 universe for every year from 2010 to 2026:
Wikipedia revisions for membership, free Yahoo prices. `scripts/longrun.py` paper-trades the no-LLM strategies
over 2011–2026. Momentum made 19.9% a year vs SPY's 14.0%, but its alpha after beta, +4.0% a year
(95% CI −4.3% to +12.1%), is not distinguishable from zero. The learned model did worse than the simple rules.

`app/tools/asof.py` gives the jailed LLM internet tools that return only what existed at the decision time:
Wikipedia revisions, SEC filings already accepted, and Internet Archive captures of news pages.
`scripts/llm_web_backtest.py` has it re-rank each month's 20 screened stocks, and `scripts/llm_web_report.py`
tests whether it beats the screen. Only 2025–2026 decisions count as evidence, because the model may remember
earlier years. See [`docs/LONG_HISTORY.md`](docs/LONG_HISTORY.md).

### Live, web-informed research

```bash
cd backend && .venv/bin/python -m app.live.research NVDA JPM
```

The jailed agent researches current prices, news, articles and SEC filings
through guarded tools (about 8–20 s per ticker on a 5070), then returns
P(up) with reasoning, sources, and a full tool trace. Web tools are live-only:
backtests refuse them because today's web already contains the future. See
[`docs/LIVE_TOOLS.md`](docs/LIVE_TOOLS.md).

### Results dashboard (Python)

```bash
cd backend && .venv/bin/pip install -e ".[ui]"
.venv/bin/streamlit run app/dashboard/app.py      # opens http://localhost:8501
```

Browse every run: scores, week-clustered confidence intervals against
"always up", accuracy and long/short growth over time, calibration, the
self-improving agent's lessons and stacker decisions, every individual
prediction, a cross-run comparison, the memorization probe, and a form to
launch new runs on your local Ollama models with live progress.

A local-first AI investment research platform: runs against **your own GPUs**
(no cloud LLM API keys required) and includes a **point-in-time sandbox** for
honestly backtesting predictions — the data connectors physically reject
anything timestamped after the date you're testing against, so a backtest
result here means what it says.

This is a sibling of [AIRP](../airp) (the cloud-oriented original), not a
fork that diverged by accident — see "How this differs from AIRP" below.

## What's actually working right now

- **Sandbox / Backtest harness** (`backend/app/sandbox/`): pick a ticker and
  a historical date, get a prediction generated using only data available as
  of that date, then see it graded against what actually happened. Every run
  self-checks that the lookahead guarantee held and reports that
  transparently. **Fully wired end-to-end**, including a TypeScript UI with
  persisted history across restarts — see `docs/SANDBOX.md`.
- **Context compression** (`backend/app/context/`): per-ticker compressed
  research material with token-budget-aware retrieval, so agents don't need
  an ever-growing prompt to know "what do we know about this ticker" —
  compression never severs the link back to evidence. See
  `docs/CONTEXT_COMPRESSION.md`.
- **Local LLM routing** (`backend/app/llm/`): configure two Ollama endpoints
  (e.g. your 5070 for reasoning, your 3060 for extraction) and the backend
  routes agent calls to the right one. See `docs/LOCAL_SETUP.md`.
- **Deterministic quant engine, evidence/claim model, debate orchestrator,
  knowledge graph schema** — carried over from AIRP unchanged, since none of
  it is LLM-provider-specific. See `docs/ARCHITECTURE.md`.
- **Web UI** (`frontend/`, Next.js + TypeScript): a working Sandbox page with
  single-run, multi-date-suite, and persisted history views; a live-research
  view is the next milestone (see `docs/ROADMAP.md`).

## Quickstart

```bash
# macOS/Linux
./scripts/setup.sh
# Windows (PowerShell)
.\scripts\setup.ps1
```

Then, in two terminals:
```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload   # .venv\Scripts\Activate.ps1 on Windows
cd frontend && npm run dev
```

Open `http://localhost:3000/sandbox` and run a backtest. With no LLM
endpoints configured, everything still works — the quant/sandbox mechanism
doesn't depend on an LLM at all; only the (not-yet-UI-wired) narrative
synthesis agents would fall back to a mock response. See
`docs/CROSS_PLATFORM.md` for OS-specific notes.

## Quickstart (with your two GPUs)

See `docs/LOCAL_SETUP.md` for the full walkthrough: installing Ollama on
both machines, exposing it to your LAN, picking models sized for a 5070 and
a 3060, and pointing `backend/.env` at both.

## Repository layout

```
airp-local/
├── scripts/                setup.sh (macOS/Linux) and setup.ps1 (Windows)
├── backend/
│   ├── app/
│   │   ├── llm/            LLMClient protocol, Ollama-compatible client, GPU router
│   │   ├── sandbox/        point-in-time clock, lookahead prevention, backtest runner
│   │   ├── context/        per-ticker compressed context store, token-budget retrieval
│   │   ├── store/          SQLite-backed backtest history (stdlib only, no compiled deps)
│   │   ├── quant/          deterministic valuation/risk/options library (unchanged from AIRP)
│   │   ├── evidence/       claim + provenance model (unchanged from AIRP)
│   │   ├── debate/         adversarial multi-agent orchestrator (unchanged from AIRP)
│   │   ├── agents/         specialist agents
│   │   ├── data_ingestion/ connectors — sandbox-aware mock connectors included
│   │   └── api/routes/     FastAPI routes, including /api/sandbox/*
│   └── tests/              pytest — 332 tests
├── frontend/               Next.js + TypeScript UI (Sandbox page is live)
└── docs/                   ARCHITECTURE, API_SPEC, AGENT_INTERFACES, ROADMAP,
                            LOCAL_SETUP (two-GPU walkthrough), SANDBOX,
                            CONTEXT_COMPRESSION, CROSS_PLATFORM
```

## How this differs from AIRP (the original cloud-oriented repo)

| | AIRP | AIRP Local |
|---|---|---|
| LLM provider | Anthropic API | Your own Ollama endpoints, routed by role across up to 2 GPUs |
| Persistence | Postgres + Neo4j required | SQLite (stdlib, zero compiled deps) by default; Postgres/Neo4j optional extras |
| Backtesting | Not built (flagged as the top ROADMAP gap) | Built: `app/sandbox/`, enforced at the connector level, self-checking, persisted history |
| Context management | Not built | `app/context/`: compressed, token-budgeted, evidence-preserving |
| UI | Not built | Next.js/TypeScript, Sandbox page functional today |
| Default install | Includes asyncpg/neo4j (platform-specific wheels) | Minimal — no compiled deps in the default install, `neo4j` is an opt-in extra |
| Quant engine, evidence model, debate orchestrator | Same code | Same code — these never depended on the LLM provider or persistence choice |

If you want the cloud/Postgres/Neo4j-backed version, that's the other repo.
This one is for running entirely on hardware you own, and for being able to
actually check whether the predictions are any good before trusting them.

## Verified state (as of last commit)

Everything below was re-run for this repo; claims inherited from upstream that weren't re-checked were removed.

- **CI is green on GitHub** (it had failed on every push until 2026-09-14, and again on 2026-09-16 when a new pandas-stubs release broke one type check; fixed the same day): `ruff`, strict `mypy` on 60 modules
  plus `mypy` on all 85, the full pytest suite, and an 85% coverage floor (currently 95%) on the
  safety-critical modules. Hosted runners have no bubblewrap, so the jailed variants skip there and the same
  checks run unjailed; locally (Ubuntu 26.04, bubblewrap installed) the jailed variants run too.
- **Reproducibility:** `python scripts/reproduce.py` re-runs every frozen backtest from the committed LLM cache
  and compares every score and individual prediction with the published files: all identical, no GPU needed.
- **Isolation:** every walk-forward run probes the jail live at start and end, and refuses to run if the agent
  can read the data or reach the network. Hostile-worker tests cover memory, CPU, hangs, floods, and
  oversized messages.
- **Web tools:** the network guard connects only to the exact public address it checked (DNS rebinding
  closed), with size and total-time limits; live research fits the model's context (measured, with
  overflow detection).
- **Results:** v5, v6 and v7 finished 2026-09-22 and were scored against criteria fixed before they ran — all
  NOT PASSED; each replays identically from its committed cache with no GPU (`scripts/reproduce.py`).
- **Forward test:** hash-chained ledger verified by the dashboard and by tests; first decision logged on time.
  **Paused 2026-09-16:** the hourly timer was uninstalled at the owner's request, so weeks from 2026-09-21 are not
  logged unless `python -m app.forward.run` is run by hand before the Monday open (missed weeks can't be backfilled).
- **Failure handling (2026-09-16):** a power loss that corrupted the LLM cache and a GPU crash (Xid 79) that made
  Ollama fall back to CPU are both handled now: damaged cache lines are skipped, CPU answers are refused, and GPU
  jobs take one shared lock.
- Frontend (`frontend/`) is the upstream Next.js app, built in CI; it has not been extended for the walk-forward
  work (the Streamlit dashboard covers that).

## License

MIT — see `LICENSE`. Produces research and trade *proposals* only; nothing
in this repository executes trades.
