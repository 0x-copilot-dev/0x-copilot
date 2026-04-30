"""PostgreSQL schema migration for durable agent runtime persistence."""

from __future__ import annotations

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.constants import Values


AGENT_RUNTIME_TABLES = (
    "agent_conversations",
    "agent_messages",
    "agent_runs",
    "runtime_events",
    "runtime_outbox_events",
    "runtime_consumer_cursors",
    "runtime_async_tasks",
    "runtime_subagent_results",
    "runtime_tool_invocations",
    "runtime_approval_requests",
    "runtime_memory_scopes",
    "runtime_memory_items",
    "runtime_context_payloads",
    "runtime_compression_events",
    "runtime_capability_snapshots",
    "runtime_audit_log",
    "runtime_legal_holds",
    "runtime_deletion_evidence",
    "runtime_checkpoints",
)


POSTGRES_AGENT_RUNTIME_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    assistant_id TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    archived_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_org_user_updated
    ON agent_conversations (org_id, user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_org_status_updated
    ON agent_conversations (org_id, status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_conversations_idempotency
    ON agent_conversations (org_id, user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    org_id TEXT NOT NULL,
    run_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    content_text TEXT NOT NULL,
    content_format TEXT NOT NULL,
    parent_message_id TEXT REFERENCES agent_messages(id),
    token_count INTEGER CHECK (token_count IS NULL OR token_count >= 0),
    trace_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('created', 'deleted')),
    created_at TIMESTAMPTZ NOT NULL,
    edited_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_org_conversation_created
    ON agent_messages (org_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_messages_org_run
    ON agent_messages (org_id, run_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL REFERENCES agent_messages(id),
    idempotency_key TEXT,
    trace_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'queued',
            'running',
            'waiting_for_approval',
            'cancelling',
            'cancelled',
            'completed',
            'failed',
            'timed_out'
        )
    ),
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    runtime_context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    runtime_version TEXT,
    request_options_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    latest_sequence_no INTEGER NOT NULL DEFAULT 0 CHECK (latest_sequence_no >= 0),
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    safe_error_code TEXT,
    safe_error_message TEXT
);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_agent_messages_run'
    ) THEN
        ALTER TABLE agent_messages
            ADD CONSTRAINT fk_agent_messages_run
            FOREIGN KEY (run_id) REFERENCES agent_runs(id);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_idempotency
    ON agent_runs (org_id, user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_org_conversation_created
    ON agent_runs (org_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_org_status_started
    ON agent_runs (org_id, status, started_at);

CREATE TABLE IF NOT EXISTS runtime_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    org_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    event_protocol_version INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    parent_event_id TEXT,
    span_id TEXT,
    parent_span_id TEXT,
    parent_task_id TEXT,
    task_id TEXT,
    subagent_id TEXT,
    display_title TEXT,
    summary TEXT,
    status TEXT,
    trace_id TEXT NOT NULL,
    payload_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    visibility TEXT NOT NULL CHECK (visibility IN ('user', 'internal', 'audit')),
    redaction_state TEXT NOT NULL CHECK (redaction_state IN ('redacted', 'truncated', 'offloaded')),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_events_run_sequence
    ON runtime_events (run_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_runtime_events_org_run_sequence
    ON runtime_events (org_id, run_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_runtime_events_org_conversation_created
    ON runtime_events (org_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_events_org_trace
    ON runtime_events (org_id, trace_id);

CREATE TABLE IF NOT EXISTS runtime_outbox_events (
    id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'completed', 'retry', 'dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL,
    locked_by TEXT,
    lock_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_status_available
    ON runtime_outbox_events (status, available_at);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_locked
    ON runtime_outbox_events (locked_by, lock_expires_at);
CREATE INDEX IF NOT EXISTS idx_runtime_outbox_org_aggregate
    ON runtime_outbox_events (org_id, aggregate_type, aggregate_id);

CREATE TABLE IF NOT EXISTS runtime_consumer_cursors (
    consumer_name TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    last_sequence_no INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence_no >= 0),
    last_event_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (consumer_name, run_id)
);

CREATE TABLE IF NOT EXISTS runtime_async_tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    org_id TEXT NOT NULL,
    parent_task_id TEXT,
    subagent_name TEXT NOT NULL,
    thread_id TEXT,
    langgraph_run_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'cancelled', 'failed', 'timed_out')
    ),
    objective_summary TEXT NOT NULL,
    constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_contract_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    timeout_seconds INTEGER CHECK (timeout_seconds IS NULL OR timeout_seconds > 0),
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    safe_error_code TEXT,
    safe_error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_async_tasks_org_run_status
    ON runtime_async_tasks (org_id, run_id, status);
CREATE INDEX IF NOT EXISTS idx_runtime_async_tasks_org_subagent_status
    ON runtime_async_tasks (org_id, subagent_name, status);
CREATE INDEX IF NOT EXISTS idx_runtime_async_tasks_org_thread
    ON runtime_async_tasks (org_id, thread_id);

CREATE TABLE IF NOT EXISTS runtime_subagent_results (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES runtime_async_tasks(id),
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    org_id TEXT NOT NULL,
    response_text TEXT,
    execution_summary TEXT,
    plan_summary TEXT,
    artifacts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    recent_messages_ref TEXT,
    error_json JSONB,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_subagent_results_task
    ON runtime_subagent_results (task_id);
CREATE INDEX IF NOT EXISTS idx_runtime_subagent_results_run_created
    ON runtime_subagent_results (run_id, created_at);

CREATE TABLE IF NOT EXISTS runtime_tool_invocations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    task_id TEXT REFERENCES runtime_async_tasks(id),
    org_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    connector_slug TEXT,
    side_effect_class TEXT NOT NULL CHECK (
        side_effect_class IN ('read', 'write', 'external_side_effect', 'destructive')
    ),
    call_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'queued',
            'running',
            'waiting_for_approval',
            'completed',
            'failed',
            'cancelled'
        )
    ),
    args_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    approval_id TEXT,
    external_ref TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    safe_error_code TEXT,
    safe_error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_tool_invocations_org_run_started
    ON runtime_tool_invocations (org_id, run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runtime_tool_invocations_org_connector_tool
    ON runtime_tool_invocations (org_id, connector_slug, tool_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_tool_invocations_run_call
    ON runtime_tool_invocations (run_id, call_id)
    WHERE call_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime_approval_requests (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    tool_invocation_id TEXT REFERENCES runtime_tool_invocations(id),
    org_id TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    risk_class TEXT NOT NULL CHECK (risk_class IN ('low', 'medium', 'high')),
    action_summary TEXT NOT NULL,
    request_payload_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by_user_id TEXT,
    decision_reason TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ
);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_runtime_tool_invocations_approval'
    ) THEN
        ALTER TABLE runtime_tool_invocations
            ADD CONSTRAINT fk_runtime_tool_invocations_approval
            FOREIGN KEY (approval_id) REFERENCES runtime_approval_requests(id);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_runtime_approval_requests_org_user_status_created
    ON runtime_approval_requests (org_id, requested_by_user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_approval_requests_org_run_status
    ON runtime_approval_requests (org_id, run_id, status);

CREATE TABLE IF NOT EXISTS runtime_memory_scopes (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT,
    assistant_id TEXT,
    scope_type TEXT NOT NULL CHECK (
        scope_type IN ('user', 'organization', 'assistant', 'conversation')
    ),
    namespace_hash TEXT NOT NULL,
    namespace_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_memory_scopes_org_scope_namespace
    ON runtime_memory_scopes (org_id, scope_type, namespace_hash);
CREATE INDEX IF NOT EXISTS idx_runtime_memory_scopes_org_user_scope
    ON runtime_memory_scopes (org_id, user_id, scope_type);

CREATE TABLE IF NOT EXISTS runtime_memory_items (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES runtime_memory_scopes(id),
    org_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_ref TEXT NOT NULL,
    content_summary TEXT,
    checksum TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by_run_id TEXT REFERENCES agent_runs(id),
    updated_by_run_id TEXT REFERENCES agent_runs(id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_memory_items_scope_path_active
    ON runtime_memory_items (scope_id, path)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_runtime_memory_items_org_scope_updated
    ON runtime_memory_items (org_id, scope_id, updated_at);

CREATE TABLE IF NOT EXISTS runtime_context_payloads (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    task_id TEXT REFERENCES runtime_async_tasks(id),
    tool_invocation_id TEXT REFERENCES runtime_tool_invocations(id),
    org_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('tool_result', 'context', 'artifact', 'checkpoint')),
    storage_backend TEXT NOT NULL CHECK (storage_backend IN ('postgres', 'object_storage', 'local_file')),
    storage_uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    mime_type TEXT,
    redaction_state TEXT NOT NULL CHECK (redaction_state IN ('redacted', 'truncated', 'offloaded')),
    retention_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_context_payloads_org_run_created
    ON runtime_context_payloads (org_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_context_payloads_org_retention
    ON runtime_context_payloads (org_id, retention_until);

CREATE TABLE IF NOT EXISTS runtime_compression_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    org_id TEXT NOT NULL,
    before_tokens INTEGER NOT NULL CHECK (before_tokens >= 0),
    after_tokens INTEGER NOT NULL CHECK (after_tokens >= 0),
    strategy TEXT NOT NULL,
    payload_refs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_compression_events_org_run_created
    ON runtime_compression_events (org_id, run_id, created_at);

CREATE TABLE IF NOT EXISTS runtime_capability_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    org_id TEXT NOT NULL,
    capability_type TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    capability_version TEXT,
    scopes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_class TEXT,
    summary TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_capability_snapshots_org_run_type
    ON runtime_capability_snapshots (org_id, run_id, capability_type);
CREATE INDEX IF NOT EXISTS idx_runtime_capability_snapshots_org_name
    ON runtime_capability_snapshots (org_id, capability_name);

CREATE TABLE IF NOT EXISTS runtime_audit_log (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'runtime', 'worker', 'system')),
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    run_id TEXT REFERENCES agent_runs(id),
    trace_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'denied')),
    metadata_json_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_audit_log_org_user_created
    ON runtime_audit_log (org_id, user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_audit_log_org_resource
    ON runtime_audit_log (org_id, resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_runtime_audit_log_org_run_created
    ON runtime_audit_log (org_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_audit_log_org_trace
    ON runtime_audit_log (org_id, trace_id);

CREATE TABLE IF NOT EXISTS runtime_legal_holds (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT,
    scope TEXT NOT NULL CHECK (scope IN ('org', 'user', 'conversation')),
    resource_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    released_by_user_id TEXT,
    released_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_runtime_legal_holds_org_resource_active
    ON runtime_legal_holds (org_id, scope, resource_id)
    WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS runtime_deletion_evidence (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    reason TEXT,
    conversations_archived INTEGER NOT NULL DEFAULT 0,
    messages_tombstoned INTEGER NOT NULL DEFAULT 0,
    runs_cancelled INTEGER NOT NULL DEFAULT 0,
    events_retained INTEGER NOT NULL DEFAULT 0,
    audit_event_id TEXT REFERENCES runtime_audit_log(id),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_deletion_evidence_org_user_created
    ON runtime_deletion_evidence (org_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS runtime_checkpoints (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_namespace TEXT NOT NULL,
    checkpoint_version INTEGER NOT NULL CHECK (checkpoint_version > 0),
    checkpoint_blob_ref TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_checkpoints_org_thread_namespace_version
    ON runtime_checkpoints (org_id, thread_id, checkpoint_namespace, checkpoint_version);
CREATE INDEX IF NOT EXISTS idx_runtime_checkpoints_org_thread_created
    ON runtime_checkpoints (org_id, thread_id, created_at);
"""


class PostgresMigration(RuntimeContract):
    """One deterministic PostgreSQL migration."""

    migration_id: str
    sql: str


class PostgresMigrationCatalog:
    """Migration catalog for CI validation and future PostgreSQL adapters."""

    @classmethod
    def initial_runtime_persistence(cls) -> PostgresMigration:
        """Return the first migration implementing runtime persistence tables."""

        return PostgresMigration(
            migration_id=Values.MIGRATION_ID,
            sql=POSTGRES_AGENT_RUNTIME_MIGRATION_SQL.strip(),
        )

    @classmethod
    def ordered_migrations(cls) -> tuple[PostgresMigration, ...]:
        """Return migrations in deterministic apply order."""

        return (cls.initial_runtime_persistence(),)
