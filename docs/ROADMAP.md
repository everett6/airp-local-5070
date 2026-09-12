# Roadmap

Milestones are ordered by what unblocks the most subsequent work, not by
layer number. "Done" criteria are concrete and testable — a milestone isn't
complete because code exists for it, but because its done-criteria pass.

## v0.1 — Current state (this skeleton)

Deterministic quant engine, evidence/claim model, confidence engine, debate
state machine, one reference specialist agent (Fundamental Analyst) and the
Bull/Bear thesis agents, mock data connectors, Postgres/Neo4j schemas, 33
passing unit tests covering quant correctness, evidence verification, and
orchestrator fault tolerance.

**Explicitly not done**: most of the specialist roster (see
`docs/AGENT_INTERFACES.md`'s status table), real data connectors, the
frontend, Docker/CI, wired API routes, and any backtesting harness.

## v0.2 — End-to-end single-ticker run

**Goal**: `POST /api/research {"ticker": "ACME"}` → poll status → `GET
/api/reports/{run_id}` returns a real rendered report, using mock data
connectors (no paid API keys required yet).

- [ ] Wire `api/routes/research.py`'s background task per `docs/API_SPEC.md`'s
      "Constructing the orchestrator" section.
- [ ] Implement `MacroEconomist`, `RiskManager`, `PortfolioManager` agents
      (currently referenced by the orchestrator but not implemented) — thin
      wrappers around existing `quant/risk.py` and `quant/ratios.py`
      functions, following the Fundamental Analyst pattern.
- [ ] Persist `DebateRunState` output to Postgres (`reports`, `claims`,
      `claim_evidence`, `evidence_refs` tables already exist in `db/schema.sql`).
- [ ] Add an integration test that runs the full pipeline against mock
      connectors and asserts a report renders with `verification.blocking == False`.

**Done when**: the integration test above is green in CI, and a person can
run one CLI command locally and get back a markdown file citing real (mock)
evidence for every number.

## v0.3 — Docker Compose + CI

- [ ] `docker-compose.yml`: postgres (with pgvector extension), neo4j, redis,
      backend, frontend.
- [ ] `.github/workflows/ci.yml`: lint (ruff), type-check (mypy --strict on
      `app/quant`, `app/evidence`, `app/confidence` first — the modules with
      zero LLM dependency, where strict typing has the best cost/benefit),
      pytest with coverage gate on `app/quant` (target: 90%+, since it's the
      most safety-critical code in the repo).
- [ ] Alembic migration matching `db/schema.sql` (currently a single hand-written
      SQL file; migrations are needed before any schema change post-v0.2 data).

**Done when**: `docker compose up --build` on a clean machine gets you a
working `/api/health` and `/docs` with zero manual steps beyond `cp
.env.example .env`.

## v0.4 — Real data connectors (behind existing interfaces)

Each of these implements `DataConnector` exactly as `MockMarketDataConnector`
does — no changes needed above the connector layer.

- [ ] `PolygonMarketDataConnector` / equivalent for quotes + options chains
- [ ] Real `SECFilingsConnector._fetch_raw` against EDGAR submissions API
      (`data.sec.gov/submissions/CIK##########.json`) + full-text search
- [ ] FRED connector for economic indicators
- [ ] A news connector (provider TBD — cost/coverage tradeoff to evaluate)
- [ ] Earnings transcript connector

**Done when**: a research run for a real, liquid ticker (e.g. a large-cap)
produces a report where every `EvidenceRef` resolves to a real, dated source,
and `data_quality_score` reflects genuine provider completeness rather than a
mock constant.

## v0.5 — Remaining specialist roster + knowledge graph population

- [ ] Technical Analyst (`quant/technicals.py` needs writing: moving averages,
      RSI, MACD, Bollinger Bands as pure functions — same pattern as
      `quant/ratios.py`, same "no LLM math" rule)
- [ ] Options Strategist / Volatility Analyst (tools already exist in
      `quant/options.py`; agent wiring does not)
- [ ] Industry Expert / Supply Chain / Competition Analyst (knowledge-graph
      traversal agents — first real consumers of `neo4j_client.neighbors`)
- [ ] Valuation Expert, Quant Researcher, Factor Model Researcher
- [ ] Compliance Agent — deliberately rule-based, not LLM (see
      `docs/AGENT_INTERFACES.md`'s roster table for why)
- [ ] Knowledge graph ingestion pipeline: turn normalized filings/transcripts
      into `GraphNode`/`GraphRelationship` writes (currently the schema and
      client exist; nothing populates the graph yet)

**Done when**: all 21 roles in `AgentRole` have a working `analyze()`
implementation, and a research run's debate transcript shows genuine
disagreement being surfaced (not just Bull/Bear, but e.g. Risk Manager
flagging something the Portfolio Manager's proposed sizing ignored).

## v0.6 — Multi-tenant hardening

- [ ] Auth layer (see `docs/API_SPEC.md` "Auth" section)
- [ ] Row-level scoping enforcement on `research_runs`/`reports`/`memory_records`
      by authenticated identity (schema already supports this — see
      `docs/ARCHITECTURE.md` §9)
- [ ] Rate limiting / per-user quota on `POST /api/research`
- [ ] Secrets moved from `.env` to a secrets manager for any non-local deployment

**Done when**: two different authenticated users cannot see each other's
research runs even with a shared database, verified by an integration test
that asserts a 403/404 on cross-tenant access attempts.

## v1.0 — Evaluation framework & backtesting harness

This is the highest-leverage remaining gap flagged in
`docs/ARCHITECTURE.md` §12: nothing currently validates the pipeline against
historical outcomes before it's trusted on a live ticker.

- [ ] Historical replay tool: given a past date and ticker, reconstruct the
      data snapshot as of that date (using `data_snapshot_ids` versioning
      already built into `research_runs`) and run the full pipeline as if it
      were "live" then, so its recommendation can be checked against what
      actually happened.
- [ ] A benchmark suite of past ticker/date pairs with known outcomes
      (earnings beats/misses, large price moves) to run the replay tool
      against in CI, catching regressions in the debate/quant pipeline the
      same way `tests/` catches regressions in individual functions.
- [ ] Wire `memory/retrieval.py::build_calibration_report` into a scheduled
      job that scores completed runs against realized outcomes and writes
      `MemoryKind.PREDICTION_ACCURACY` records automatically, rather than
      requiring manual `record_outcome` calls.
- [ ] A calibration dashboard (frontend) showing confidence-score vs.
      actual-accuracy calibration curves per agent namespace — this is what
      turns "the system says 74% confidence" into a checkable claim rather
      than an assertion.

**Done when**: a maintainer can answer "when this system says 70-80%
confidence, how often is it actually right?" with a number pulled from real
accumulated data, not a guess.

## Deliberately deferred indefinitely

- **Autonomous execution.** Not a milestone; see `docs/ARCHITECTURE.md` §8.
  If this is ever built, it should be a completely separate system consuming
  `portfolio_proposals` rows with its own independent risk controls and human
  sign-off gates — not an extension of this codebase's trust boundary.
- **A single unified "mega-prompt" fallback mode.** Occasionally tempting for
  quick demos ("just ask one big model to do all of this"), but it would
  undermine every explainability guarantee this system exists to provide, so
  it's out of scope even as an opt-in mode.
