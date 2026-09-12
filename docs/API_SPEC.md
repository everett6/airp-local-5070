# API Specification

Base path: `/api`. All bodies are JSON. This documents the intended contract;
`app/api/routes/*.py` currently implements the shapes below with the
orchestrator wiring stubbed out (see the "Wiring status" column) — the
schemas are real and stable, the execution behind them is being filled in
per `docs/ROADMAP.md`.

## `GET /api/health`

Liveness check. `200 {"status": "ok"}`. No auth required; used by the
container orchestrator's health probe.

## `POST /api/research`

Starts a research run for a ticker.

**Request**
```json
{
  "ticker": "ACME",
  "include_options": true,
  "include_macro": true
}
```

**Response `202`**
```json
{ "run_id": "b3c1...", "status": "queued" }
```

Wiring status: returns a `run_id` and enqueues nothing yet — the
`background_tasks.add_task(...)` call in `research.py` is present but
commented out pending the dependency-injection wiring described in
"Constructing the orchestrator" below.

## `GET /api/research/{run_id}/status`

```json
{ "run_id": "b3c1...", "status": "running" }
```

`status` is one of `queued | running | completed | failed`, matching the
`research_runs.status` check constraint in `db/schema.sql`.

## `GET /api/reports/{run_id}`

Returns the rendered report and its structured metadata once
`status == "completed"`.

**Response `200`**
```json
{
  "run_id": "b3c1...",
  "ticker": "ACME",
  "rendered_markdown": "# Research Report: ACME\n...",
  "overall_confidence": 0.74,
  "confidence_breakdown": {
    "evidence_quality": 0.9,
    "data_quality": 0.85,
    "reasoning_quality": 0.7,
    "consensus_strength": 0.6,
    "market_uncertainty_penalty": 0.8,
    "caveats": ["1 claim(s) could not be verified..."]
  },
  "verification_summary": {
    "total_claims": 24, "verified": 20, "stale": 1, "unverified": 3, "contradicted": 0
  }
}
```

`404` if the run doesn't exist or hasn't completed. Wiring status: not yet
backed by the `reports` table — currently always 404.

## `GET /api/reports/{run_id}/evidence`

Returns the full claim → evidence graph for audit: every `Claim` produced
during the run, its `verification_status`, and the resolved `EvidenceRef`(s)
backing it. This is the endpoint the frontend's "show your work" view reads
from — it should never need to re-derive citations from the rendered
markdown.

**Response `200`**
```json
{
  "run_id": "b3c1...",
  "claims": [
    {
      "claim_id": "ACME:latest:dcf_fv",
      "text": "DCF fair value/share for ACME (latest) is 142.3100",
      "made_by": "fundamental_analyst",
      "verification_status": "verified",
      "is_numeric": true,
      "numeric_value": 142.31,
      "numeric_source": "quant.dcf.discounted_cash_flow",
      "evidence_refs": [
        {
          "evidence_id": "a1b2c3...",
          "source_type": "financial_statement",
          "source_id": "10-K:2026-02-15",
          "retrieved_at": "2026-09-01T12:00:00Z",
          "data_quality_score": 0.92
        }
      ]
    }
  ]
}
```

Wiring status: not yet backed by the `claims`/`claim_evidence`/`evidence_refs`
tables — currently always 404.

## `GET /api/agents`

```json
{ "agents": ["chief_investment_officer", "fundamental_analyst", "..."] }
```

Static list from `AgentRole` — useful for the frontend to render a debate
transcript's sender labels without hardcoding the enum.

## Endpoints intentionally not present in v1

- **No `POST /api/trades`, no execution endpoint of any kind.** This is a
  structural decision, not a v1 scoping gap — see `docs/ARCHITECTURE.md` §8.
  A future `portfolio_proposals`-reading endpoint (`GET
  /api/reports/{run_id}/portfolio`) is planned; it will always return a
  *proposal* the human still has to act on elsewhere.
- **No agent-to-agent debugging endpoint that exposes raw prompts/completions.**
  The audit trail (`GET /api/research/{run_id}/transcript`, planned) will
  expose the same `AgentMessage` structure agents actually exchange — never
  raw model I/O, which could leak provider-specific implementation details or
  make it easy to reconstruct exact prompts for injection testing against
  production.

## Constructing the orchestrator (server-side wiring, not a public endpoint)

`api/routes/research.py`'s background task is expected to:

1. Build a `MessageBus()` and a `MemoryStore` (Postgres-backed in prod,
   `InMemoryStore` in dev/tests).
2. Construct each `SpecialistAgent` with its real tool dict — e.g. the
   Fundamental Analyst's tools map `"dcf_valuation"` to a thin wrapper around
   `quant.dcf.discounted_cash_flow`, `"get_normalized_financials"` to a
   connector call, etc. This mapping is intentionally explicit and manual
   (not auto-wired by introspection) so the allow-list is always a reviewable
   diff — see `docs/ARCHITECTURE.md` §2 and §9.
3. Instantiate `DebateOrchestrator(bus, agents)` and call
   `orchestrator.run(ctx, independent_roles=[...], current_price=...)`.
4. Feed the resulting `DebateRunState` into `EvidenceVerifier.verify_bundle`
   for each `ClaimBundle`, then `confidence.engine.compute_confidence`, then
   `reports.generator.render_report`.
5. Persist the `DebateRunState.errors`, the verification report, the
   confidence breakdown, and the rendered markdown to Postgres, and flip
   `research_runs.status` to `completed` (or `failed` if the orchestrator
   itself raised, which it's designed not to do — see the fault-tolerance
   model — but the API layer should not assume that guarantee blindly).

This is deliberately not exposed as its own endpoint; it is what "start a
research run" *means* on the backend, not a separate capability a caller can
invoke piecemeal — piecemeal invocation would let a caller skip verification
or confidence scoring, which defeats the point of the whole pipeline.

## Error format

All error responses follow FastAPI's default `{"detail": "..."}` shape in
this skeleton. Production hardening should move to a structured error body
(`{"error_code": "...", "message": "...", "run_id": "..."}`) once client
error-handling requirements are known — tracked in `docs/ROADMAP.md`.

## Auth

Not implemented in this skeleton. The intended shape (per
`docs/ARCHITECTURE.md` §9) is a bearer-token or session auth layer in front
of every route under `/api`, with `requested_by` on `research_runs` populated
from the authenticated identity rather than accepted as a client-supplied
field.
