-- B1: Denormalized per-run token usage table.
--
-- Token usage is already extracted by ``runtime_worker.run_metrics`` and
-- emitted on RUN_COMPLETED events, but it lives only inside
-- ``runtime_events.payload_json_redacted``. Aggregating "tokens used by
-- user X this month" by scanning JSONB doesn't scale past tens of
-- thousands of runs. One denormalized row per run, populated by the
-- worker, lets the read endpoints (B4) and rollups answer in milliseconds.
--
-- Idempotency: written via INSERT ... ON CONFLICT (run_id) DO NOTHING so
-- worker retries / re-handling never double-charges.
--
-- Retention is decoupled from messages: when a user deletes their
-- conversation history the row's ``pii_purged_at`` is stamped instead of
-- the row being deleted, so billing and audit aggregates remain intact
-- even after PII is severed.

CREATE TABLE IF NOT EXISTS runtime_run_usage (
    id                    TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    conversation_id       TEXT NOT NULL REFERENCES agent_conversations(id),
    run_id                TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
    assistant_id          TEXT,
    model_provider        TEXT NOT NULL,
    model_name            TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens         INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cached_input_tokens   INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
    total_tokens          INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    chunk_count           INTEGER NOT NULL DEFAULT 0,
    first_token_ms        INTEGER,
    duration_ms           INTEGER NOT NULL DEFAULT 0,
    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ NOT NULL,
    status                TEXT NOT NULL,
    schema_version        INTEGER NOT NULL DEFAULT 1,
    retention_until       TIMESTAMPTZ,
    pii_purged_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL
);

-- Compound indexes lead with org_id per the project's tenant-scoping rule.
CREATE INDEX IF NOT EXISTS idx_runtime_run_usage_org_user_completed
    ON runtime_run_usage (org_id, user_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_run_usage_org_conversation_completed
    ON runtime_run_usage (org_id, conversation_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_run_usage_org_completed
    ON runtime_run_usage (org_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_run_usage_org_model_completed
    ON runtime_run_usage (org_id, model_provider, model_name, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_run_usage_retention
    ON runtime_run_usage (retention_until) WHERE pii_purged_at IS NULL;
