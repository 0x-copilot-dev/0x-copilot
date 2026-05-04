-- B2: Per-LLM-call token usage table.
--
-- Run-level usage (B1) answers "how many tokens this run". To answer
-- "where did the tokens go?" — main supervisor vs each subagent vs each
-- LLM call — we need per-LLM-call rows. One row per AIMessage that
-- closes with usage. This is the foundation Claude-Code-style /context
-- (B5) is built on.
--
-- task_id and subagent_id are populated when the call ran inside a
-- subagent. The task_id index is partial WHERE task_id IS NOT NULL so
-- per-subagent attribution stays fast without bloating the index for
-- the main-graph rows.
--
-- No FK to runtime_async_tasks(task_id): async tasks may be reaped
-- before usage queries run.

CREATE TABLE IF NOT EXISTS runtime_model_call_usage (
    id                    TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL,
    run_id                TEXT NOT NULL REFERENCES agent_runs(id),
    conversation_id       TEXT NOT NULL REFERENCES agent_conversations(id),
    parent_event_id       TEXT,
    trace_id              TEXT NOT NULL,
    task_id               TEXT,
    subagent_id           TEXT,
    model_provider        TEXT NOT NULL,
    model_name            TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens         INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cached_input_tokens   INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
    total_tokens          INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    duration_ms           INTEGER NOT NULL DEFAULT 0,
    schema_version        INTEGER NOT NULL DEFAULT 1,
    cost_micro_usd        BIGINT,
    pricing_id            TEXT,
    pricing_version       TEXT,
    created_at            TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_model_call_usage_org_run
    ON runtime_model_call_usage (org_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_model_call_usage_org_trace
    ON runtime_model_call_usage (org_id, trace_id);
CREATE INDEX IF NOT EXISTS idx_runtime_model_call_usage_org_task
    ON runtime_model_call_usage (org_id, task_id) WHERE task_id IS NOT NULL;
