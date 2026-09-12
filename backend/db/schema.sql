-- AIRP structured storage. Postgres owns everything relational and
-- transactional (runs, reports, evidence index, memory metadata + vectors
-- via pgvector). The knowledge graph (entities/relationships) lives in Neo4j;
-- this schema stores only a pointer (`kg_node_id`) where relevant, so the two
-- stores stay loosely coupled and independently scalable.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Research runs ────────────────────────────────────────────────────────
CREATE TABLE research_runs (
    run_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker            TEXT NOT NULL,
    requested_by      TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    status            TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed')),
    agent_versions    JSONB NOT NULL DEFAULT '{}',   -- {role: version} pinned for reproducibility
    prompt_versions   JSONB NOT NULL DEFAULT '{}',
    data_snapshot_ids JSONB NOT NULL DEFAULT '{}',   -- {connector_name: source_id} pinned inputs
    random_seed       INTEGER NOT NULL DEFAULT 42
);
CREATE INDEX idx_research_runs_ticker ON research_runs (ticker);

-- ── Agent messages (full audit transcript, mirrors MessageBus.history) ───
CREATE TABLE agent_messages (
    message_id      UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    topic           TEXT NOT NULL,
    sender          TEXT NOT NULL,
    message_type    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    payload_schema  TEXT NOT NULL,
    payload         JSONB NOT NULL,
    evidence_ids    TEXT[] NOT NULL DEFAULT '{}',
    in_reply_to     TEXT[] NOT NULL DEFAULT '{}',
    agent_version   TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
CREATE INDEX idx_agent_messages_run ON agent_messages (run_id);

-- ── Evidence ──────────────────────────────────────────────────────────────
CREATE TABLE evidence_refs (
    evidence_id         TEXT PRIMARY KEY,
    source_type         TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    ticker              TEXT,
    retrieved_at        TIMESTAMPTZ NOT NULL,
    published_at        TIMESTAMPTZ,
    excerpt_hash        TEXT,
    url_or_locator      TEXT,
    data_quality_score  REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX idx_evidence_ticker ON evidence_refs (ticker);
CREATE INDEX idx_evidence_source_type ON evidence_refs (source_type);

CREATE TABLE claims (
    claim_id             TEXT PRIMARY KEY,
    run_id               UUID NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    text                 TEXT NOT NULL,
    made_by              TEXT NOT NULL,
    verification_status  TEXT NOT NULL,
    is_numeric           BOOLEAN NOT NULL DEFAULT FALSE,
    numeric_value         DOUBLE PRECISION,
    numeric_source        TEXT,
    CHECK (NOT is_numeric OR numeric_source IS NOT NULL)
);
CREATE TABLE claim_evidence (
    claim_id     TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id  TEXT NOT NULL REFERENCES evidence_refs(evidence_id),
    PRIMARY KEY (claim_id, evidence_id)
);

-- ── Reports ───────────────────────────────────────────────────────────────
CREATE TABLE reports (
    run_id              UUID PRIMARY KEY REFERENCES research_runs(run_id) ON DELETE CASCADE,
    rendered_markdown   TEXT NOT NULL,
    overall_confidence  REAL NOT NULL,
    confidence_breakdown JSONB NOT NULL,
    verification_summary JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Long-term memory (Layer 3) ───────────────────────────────────────────
CREATE TABLE memory_records (
    memory_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind            TEXT NOT NULL CHECK (kind IN
                      ('thesis','report','recommendation','mistake',
                       'market_reaction','earnings_outcome','prediction_accuracy')),
    namespace       TEXT NOT NULL,
    ticker          TEXT,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence      REAL NOT NULL,
    source_run_id   UUID REFERENCES research_runs(run_id),
    citations       TEXT[] NOT NULL DEFAULT '{}',
    content         JSONB NOT NULL,
    outcome         JSONB,
    retrieval_tags  TEXT[] NOT NULL DEFAULT '{}',
    embedding       VECTOR(1536)
);
CREATE INDEX idx_memory_namespace ON memory_records (namespace);
CREATE INDEX idx_memory_ticker ON memory_records (ticker);
CREATE INDEX idx_memory_embedding ON memory_records USING ivfflat (embedding vector_cosine_ops);

-- ── Portfolio proposals (Layer 9) ────────────────────────────────────────
CREATE TABLE portfolio_proposals (
    proposal_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id            UUID NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
    ticker            TEXT NOT NULL,
    proposed_weight   REAL NOT NULL,
    sector            TEXT NOT NULL,
    breaches_position_limit BOOLEAN NOT NULL,
    breaches_sector_limit   BOOLEAN NOT NULL,
    rationale         TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Data ingestion provenance/versioning (Layer 1) ───────────────────────
CREATE TABLE ingested_records (
    source_id         TEXT NOT NULL,
    connector_name    TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    fetched_at        TIMESTAMPTZ NOT NULL,
    quality_score     REAL NOT NULL,
    validation_issues JSONB NOT NULL DEFAULT '[]',
    raw_ref           TEXT,   -- pointer to blob storage for the raw payload, if retained
    PRIMARY KEY (source_id, connector_name, fetched_at)
);
CREATE INDEX idx_ingested_connector ON ingested_records (connector_name);
