# AIRP Local

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
  transparently. **This is fully wired end-to-end**, including a real
  TypeScript UI — see `docs/SANDBOX.md`.
- **Local LLM routing** (`backend/app/llm/`): configure two Ollama endpoints
  (e.g. your 5070 for reasoning, your 3060 for extraction) and the backend
  routes agent calls to the right one. See `docs/LOCAL_SETUP.md`.
- **Deterministic quant engine, evidence/claim model, debate orchestrator,
  knowledge graph schema** — carried over from AIRP unchanged, since none of
  it is LLM-provider-specific. See `docs/ARCHITECTURE.md`.
- **Web UI** (`frontend/`, Next.js + TypeScript): a working Sandbox page
  today; a live-research view is the next milestone (see `docs/ROADMAP.md`).

## Quickstart (mock mode — no GPUs needed yet)

```bash
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

Open `http://localhost:3000/sandbox` and run a backtest. With no LLM
endpoints configured, everything still works — the quant/sandbox mechanism
doesn't depend on an LLM at all; only the (not-yet-UI-wired) narrative
synthesis agents would fall back to a mock response.

## Quickstart (with your two GPUs)

See `docs/LOCAL_SETUP.md` for the full walkthrough: installing Ollama on
both machines, exposing it to your LAN, picking models sized for a 5070 and
a 3060, and pointing `backend/.env` at both.

## Repository layout

```
airp-local/
├── backend/
│   ├── app/
│   │   ├── llm/            LLMClient protocol, Ollama-compatible client, GPU router
│   │   ├── sandbox/        point-in-time clock, lookahead prevention, backtest runner
│   │   ├── quant/          deterministic valuation/risk/options library (unchanged from AIRP)
│   │   ├── evidence/       claim + provenance model (unchanged from AIRP)
│   │   ├── debate/         adversarial multi-agent orchestrator (unchanged from AIRP)
│   │   ├── agents/         specialist agents
│   │   ├── data_ingestion/ connectors — sandbox-aware mock connectors included
│   │   └── api/routes/     FastAPI routes, including /api/sandbox/*
│   └── tests/              pytest — 67 tests, including sandbox lookahead-proof tests
├── frontend/               Next.js + TypeScript UI (Sandbox page is live)
└── docs/                   ARCHITECTURE, API_SPEC, AGENT_INTERFACES, ROADMAP,
                            LOCAL_SETUP (two-GPU walkthrough), SANDBOX (how the
                            lookahead guarantee actually works)
```

## How this differs from AIRP (the original cloud-oriented repo)

| | AIRP | AIRP Local |
|---|---|---|
| LLM provider | Anthropic API | Your own Ollama endpoints, routed by role across up to 2 GPUs |
| Persistence | Postgres + Neo4j required | In-process by default; Postgres/Neo4j optional |
| Backtesting | Not built (flagged as the top ROADMAP gap) | Built: `app/sandbox/`, enforced at the connector level, self-checking |
| UI | Not built | Next.js/TypeScript, Sandbox page functional today |
| Quant engine, evidence model, debate orchestrator | Same code | Same code — these never depended on the LLM provider or persistence choice |

If you want the cloud/Postgres/Neo4j-backed version, that's the other repo.
This one is for running entirely on hardware you own, and for being able to
actually check whether the predictions are any good before trusting them.

## Verified state (as of last commit)

- Backend: 67/67 pytest passing, `ruff check` clean, `mypy --strict` clean on
  `app/quant`/`app/evidence`/`app/confidence`.
- Frontend: `npm run build` clean under TypeScript strict mode, `eslint`
  clean, both routes verified serving over real HTTP against the live
  backend (not just unit-tested in isolation).
- The sandbox self-check (`self_check_passed`) is verified both by a
  dedicated pytest (`tests/test_sandbox.py`) and by the harness performing
  the same check live on every single run — so a regression would surface
  in production, not just in CI.

## License

MIT — see `LICENSE`. Produces research and trade *proposals* only; nothing
in this repository executes trades.
