# AIRP Local — 5070 edition

> **This repo is `airp-local-5070`, a clone of `airp-local` tuned for a single
> RTX 5070 (12 GB).** It adds a leakage-proof walk-forward test of a local LLM
> forecasting agent on **real** prices: a bubblewrap process jail (no network,
> no data access), point-in-time data and memory, anonymized inputs, a test
> window after the model's measured training cutoff, a self-improving agent
> arm, and non-LLM baselines on the identical grid. It also fixes a cache
> bypass in the original sandbox. **Headline result: across 520–1,040
> post-warm-up predictions per run, no LLM arm (qwen3 8B or 14B, plain or
> self-improving) beat simply predicting "up"** — see
> [`docs/WALKFORWARD_5070.md`](docs/WALKFORWARD_5070.md) for the full results,
> the research behind the design, and how to reproduce.

### Phase F: research-driven optimization (built 2026-09-16, GPU runs pending)

Log-prob LLM scores (the verbalized answers used only 18 distinct values), a Lookahead Propensity leak test,
Kronos and classical-anomaly baselines, an exact Newton stacker, Deflated Sharpe, and a tuned Ollama service
(measured 1.6× faster). Success criteria for the `v7_phase_f` run were pre-registered before it ran. See
[`docs/RESEARCH_OPTIMIZATION.md`](docs/RESEARCH_OPTIMIZATION.md) and `docs/EXECUTION_PLAN.md` (Phase F).
After a reboot, `scripts/phase_f_pipeline.sh` runs the remaining GPU work one job at a time.

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
│   └── tests/              pytest — 288 tests
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
- **Forward test:** hash-chained ledger verified by the dashboard and by tests; first decision logged on time.
- **Failure handling (2026-09-16):** a power loss that corrupted the LLM cache and a GPU crash (Xid 79) that made
  Ollama fall back to CPU are both handled now: damaged cache lines are skipped, CPU answers are refused, and GPU
  jobs take one shared lock.
- Frontend (`frontend/`) is the upstream Next.js app, built in CI; it has not been extended for the walk-forward
  work (the Streamlit dashboard covers that).

## License

MIT — see `LICENSE`. Produces research and trade *proposals* only; nothing
in this repository executes trades.
