-- 0001_baseline — the complete ai-backend runtime schema (pre-launch squash,
-- 2026-07-21). Squashed from migrations 0001..0033 with zero installed
-- deployments; old files remain in git history. Generated from pg_dump of
-- the fully-migrated schema and verified equivalent by catalog diff; future
-- migrations start at 0002. Wipe pre-squash dev databases once.

CREATE FUNCTION runtime_audit_log_immutable_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'audit log is append-only; % on % rejected',
        TG_OP, TG_TABLE_NAME;
END;
$$;

CREATE TABLE agent_conversation_tool_ordinals (
    org_id text NOT NULL,
    conversation_id text NOT NULL,
    conversation_ordinal integer NOT NULL,
    tool_call_id text NOT NULL,
    tool_name text NOT NULL,
    run_id text NOT NULL,
    allocated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_conversation_tool_ordinals_conversation_ordinal_check CHECK ((conversation_ordinal > 0))
);

CREATE TABLE agent_conversations (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    assistant_id text NOT NULL,
    title text,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    archived_at timestamp with time zone,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    idempotency_key text,
    enabled_connectors jsonb DEFAULT '{}'::jsonb NOT NULL,
    connectors_updated_at timestamp with time zone,
    deleted_at timestamp with time zone,
    folder text,
    parent_conversation_id text,
    forked_from_share_id text,
    forked_from_message_id text,
    pinned boolean DEFAULT false NOT NULL,
    CONSTRAINT agent_conversations_status_check CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text])))
);

CREATE TABLE agent_messages (
    id text NOT NULL,
    conversation_id text NOT NULL,
    org_id text NOT NULL,
    run_id text,
    role text NOT NULL,
    content_text text NOT NULL,
    content_format text NOT NULL,
    content_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    attachments_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    quote_json jsonb,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    parent_message_id text,
    source_message_id text,
    branch_id text,
    token_count integer,
    trace_id text,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone,
    encryption_version smallint DEFAULT 0 NOT NULL,
    retention_until timestamp with time zone,
    CONSTRAINT agent_messages_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text, 'tool'::text, 'system'::text]))),
    CONSTRAINT agent_messages_status_check CHECK ((status = ANY (ARRAY['created'::text, 'deleted'::text]))),
    CONSTRAINT agent_messages_token_count_check CHECK (((token_count IS NULL) OR (token_count >= 0)))
);

CREATE TABLE agent_runs (
    id text NOT NULL,
    conversation_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    user_message_id text NOT NULL,
    idempotency_key text,
    trace_id text NOT NULL,
    status text NOT NULL,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    model_config_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    runtime_context_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    runtime_version text,
    request_options_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    latest_sequence_no integer DEFAULT 0 NOT NULL,
    row_version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    safe_error_code text,
    safe_error_message text,
    CONSTRAINT agent_runs_latest_sequence_no_check CHECK ((latest_sequence_no >= 0)),
    CONSTRAINT agent_runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'waiting_for_approval'::text, 'cancelling'::text, 'cancelled'::text, 'completed'::text, 'failed'::text, 'timed_out'::text])))
);

CREATE TABLE conversation_share_recipients (
    share_id text NOT NULL,
    user_id text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE conversation_shares (
    share_id text NOT NULL,
    org_id text NOT NULL,
    conversation_id text NOT NULL,
    created_by_user_id text NOT NULL,
    view_access text NOT NULL,
    sources_visible_to_viewer boolean DEFAULT false NOT NULL,
    share_token_hash text,
    share_token_prefix text,
    snapshot_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversation_shares_token_consistency CHECK ((((share_token_hash IS NULL) AND (share_token_prefix IS NULL)) OR ((share_token_hash IS NOT NULL) AND (share_token_prefix IS NOT NULL)))),
    CONSTRAINT conversation_shares_view_access_check CHECK ((view_access = ANY (ARRAY['workspace'::text, 'specific'::text])))
);

CREATE TABLE model_pricing (
    id text NOT NULL,
    provider text NOT NULL,
    model_name text NOT NULL,
    region text DEFAULT 'global'::text NOT NULL,
    effective_from timestamp with time zone NOT NULL,
    effective_until timestamp with time zone,
    input_per_1m_micro_usd bigint NOT NULL,
    output_per_1m_micro_usd bigint NOT NULL,
    cached_input_per_1m_micro_usd bigint,
    context_window_tokens integer,
    pricing_source text NOT NULL,
    pricing_version text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT model_pricing_input_per_1m_micro_usd_check CHECK ((input_per_1m_micro_usd >= 0)),
    CONSTRAINT model_pricing_output_per_1m_micro_usd_check CHECK ((output_per_1m_micro_usd >= 0)),
    CONSTRAINT model_pricing_pricing_source_check CHECK ((pricing_source = ANY (ARRAY['yaml-seed'::text, 'admin-override'::text, 'partner-feed'::text])))
);

CREATE TABLE retention_policies (
    id text NOT NULL,
    org_id text NOT NULL,
    scope text NOT NULL,
    resource_id text,
    kind text NOT NULL,
    ttl_seconds bigint NOT NULL,
    created_by_user_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT retention_policies_kind_check CHECK ((kind = ANY (ARRAY['messages'::text, 'events'::text, 'context_payloads'::text, 'checkpoints'::text, 'memory_items'::text]))),
    CONSTRAINT retention_policies_scope_check CHECK ((scope = ANY (ARRAY['org'::text, 'user'::text, 'conversation'::text, 'assistant'::text]))),
    CONSTRAINT retention_policies_ttl_seconds_check CHECK ((ttl_seconds > 0))
);

CREATE TABLE runtime_approval_batch_items (
    id text NOT NULL,
    batch_id text NOT NULL,
    item_index integer NOT NULL,
    decision text,
    CONSTRAINT runtime_approval_batch_items_decision_check CHECK (((decision IS NULL) OR (decision = ANY (ARRAY['approved'::text, 'rejected'::text, 'forwarded'::text])))),
    CONSTRAINT runtime_approval_batch_items_item_index_check CHECK ((item_index >= 0))
);

ALTER TABLE ONLY runtime_approval_batch_items FORCE ROW LEVEL SECURITY;

CREATE TABLE runtime_approval_batches (
    id text NOT NULL,
    run_id text NOT NULL,
    org_id text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT runtime_approval_batches_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'resuming'::text, 'resolved'::text, 'expired'::text])))
);

ALTER TABLE ONLY runtime_approval_batches FORCE ROW LEVEL SECURITY;

CREATE TABLE runtime_approval_requests (
    id text NOT NULL,
    run_id text NOT NULL,
    tool_invocation_id text,
    org_id text NOT NULL,
    requested_by_user_id text NOT NULL,
    status text NOT NULL,
    risk_class text NOT NULL,
    action_summary text NOT NULL,
    request_payload_json_redacted jsonb DEFAULT '{}'::jsonb NOT NULL,
    decided_by_user_id text,
    decision_reason text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    decided_at timestamp with time zone,
    chain_parent_approval_id text,
    forwarded_to_user_id text,
    forwarded_at timestamp with time zone,
    forwarded_decided_at timestamp with time zone,
    chain_depth integer DEFAULT 0 NOT NULL,
    CONSTRAINT runtime_approval_requests_chain_depth_check CHECK (((chain_depth >= 0) AND (chain_depth <= 3))),
    CONSTRAINT runtime_approval_requests_no_self_parent CHECK (((chain_parent_approval_id IS NULL) OR (chain_parent_approval_id <> id))),
    CONSTRAINT runtime_approval_requests_risk_class_check CHECK ((risk_class = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
    CONSTRAINT runtime_approval_requests_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'forwarded'::text])))
);

CREATE TABLE runtime_async_tasks (
    id text NOT NULL,
    run_id text NOT NULL,
    conversation_id text NOT NULL,
    org_id text NOT NULL,
    parent_task_id text,
    subagent_name text NOT NULL,
    thread_id text,
    langgraph_run_id text,
    status text NOT NULL,
    objective_summary text NOT NULL,
    constraints_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_contract_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    timeout_seconds integer,
    started_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    safe_error_code text,
    safe_error_message text,
    CONSTRAINT runtime_async_tasks_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'completed'::text, 'cancelled'::text, 'failed'::text, 'timed_out'::text]))),
    CONSTRAINT runtime_async_tasks_timeout_seconds_check CHECK (((timeout_seconds IS NULL) OR (timeout_seconds > 0)))
);

CREATE TABLE runtime_audit_log (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text,
    actor_type text NOT NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    run_id text,
    trace_id text,
    outcome text NOT NULL,
    metadata_json_redacted jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    seq bigint,
    prev_hash bytea,
    signature bytea,
    key_version smallint,
    encryption_version smallint DEFAULT 0 NOT NULL,
    CONSTRAINT runtime_audit_log_actor_type_check CHECK ((actor_type = ANY (ARRAY['user'::text, 'runtime'::text, 'worker'::text, 'system'::text]))),
    CONSTRAINT runtime_audit_log_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'failure'::text, 'denied'::text])))
);

CREATE TABLE runtime_capability_snapshots (
    id text NOT NULL,
    run_id text NOT NULL,
    org_id text NOT NULL,
    capability_type text NOT NULL,
    capability_name text NOT NULL,
    capability_version text,
    scopes_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    risk_class text,
    summary text NOT NULL,
    loaded_at timestamp with time zone NOT NULL
);

CREATE TABLE runtime_checkpoints (
    id text NOT NULL,
    org_id text NOT NULL,
    thread_id text NOT NULL,
    checkpoint_namespace text NOT NULL,
    checkpoint_version integer NOT NULL,
    checkpoint_blob_ref text NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_checkpoints_checkpoint_version_check CHECK ((checkpoint_version > 0))
);

CREATE TABLE runtime_citations (
    citation_id text NOT NULL,
    run_id text NOT NULL,
    conversation_id text NOT NULL,
    org_id text NOT NULL,
    ordinal integer NOT NULL,
    source_connector text NOT NULL,
    source_doc_id text NOT NULL,
    source_url text,
    title text NOT NULL,
    snippet text,
    freshness_at timestamp with time zone,
    source_tool_call_id text,
    encryption_version smallint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE runtime_compression_events (
    id text NOT NULL,
    run_id text NOT NULL,
    org_id text NOT NULL,
    before_tokens integer NOT NULL,
    after_tokens integer NOT NULL,
    strategy text NOT NULL,
    payload_refs_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    trace_id text,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_compression_events_after_tokens_check CHECK ((after_tokens >= 0)),
    CONSTRAINT runtime_compression_events_before_tokens_check CHECK ((before_tokens >= 0))
);

CREATE TABLE runtime_consumer_cursors (
    consumer_name text NOT NULL,
    run_id text NOT NULL,
    last_sequence_no integer DEFAULT 0 NOT NULL,
    last_event_id text,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_consumer_cursors_last_sequence_no_check CHECK ((last_sequence_no >= 0))
);

CREATE TABLE runtime_context_payload_blobs (
    id text NOT NULL,
    payload_id text NOT NULL,
    org_id text NOT NULL,
    encrypted_blob bytea NOT NULL,
    encryption_version smallint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE runtime_context_payloads (
    id text NOT NULL,
    run_id text NOT NULL,
    task_id text,
    tool_invocation_id text,
    org_id text NOT NULL,
    kind text NOT NULL,
    storage_backend text NOT NULL,
    storage_uri text NOT NULL,
    sha256 text NOT NULL,
    byte_size bigint NOT NULL,
    mime_type text,
    redaction_state text NOT NULL,
    retention_until timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_context_payloads_byte_size_check CHECK ((byte_size >= 0)),
    CONSTRAINT runtime_context_payloads_kind_check CHECK ((kind = ANY (ARRAY['tool_result'::text, 'context'::text, 'artifact'::text, 'checkpoint'::text]))),
    CONSTRAINT runtime_context_payloads_redaction_state_check CHECK ((redaction_state = ANY (ARRAY['redacted'::text, 'truncated'::text, 'offloaded'::text]))),
    CONSTRAINT runtime_context_payloads_storage_backend_check CHECK ((storage_backend = ANY (ARRAY['postgres'::text, 'object_storage'::text, 'local_file'::text])))
);

CREATE TABLE runtime_deletion_evidence (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    request_type text NOT NULL,
    reason text,
    conversations_archived integer DEFAULT 0 NOT NULL,
    messages_tombstoned integer DEFAULT 0 NOT NULL,
    runs_cancelled integer DEFAULT 0 NOT NULL,
    events_retained integer DEFAULT 0 NOT NULL,
    audit_event_id text,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE runtime_drafts (
    id text NOT NULL,
    draft_id text NOT NULL,
    version integer NOT NULL,
    org_id text NOT NULL,
    conversation_id text NOT NULL,
    run_id text,
    user_id text NOT NULL,
    title bytea NOT NULL,
    content_text bytea NOT NULL,
    target_connector text,
    target_metadata bytea,
    citation_ids text[] DEFAULT '{}'::text[] NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    encryption_version smallint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT runtime_drafts_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'send_pending_approval'::text, 'sent'::text, 'discarded'::text, 'send_failed'::text]))),
    CONSTRAINT runtime_drafts_version_check CHECK ((version > 0))
);

CREATE TABLE runtime_events (
    id text NOT NULL,
    run_id text NOT NULL,
    conversation_id text NOT NULL,
    org_id text NOT NULL,
    sequence_no integer NOT NULL,
    event_protocol_version integer DEFAULT 1 NOT NULL,
    source text NOT NULL,
    event_type text NOT NULL,
    parent_event_id text,
    span_id text,
    parent_span_id text,
    parent_task_id text,
    task_id text,
    subagent_id text,
    display_title text,
    summary text,
    status text,
    trace_id text NOT NULL,
    payload_json_redacted jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_json_redacted jsonb DEFAULT '{}'::jsonb NOT NULL,
    visibility text NOT NULL,
    redaction_state text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    activity_kind text,
    presentation_json jsonb,
    encryption_version smallint DEFAULT 0 NOT NULL,
    retention_until timestamp with time zone,
    CONSTRAINT runtime_events_redaction_state_check CHECK ((redaction_state = ANY (ARRAY['redacted'::text, 'truncated'::text, 'offloaded'::text]))),
    CONSTRAINT runtime_events_sequence_no_check CHECK ((sequence_no > 0)),
    CONSTRAINT runtime_events_visibility_check CHECK ((visibility = ANY (ARRAY['user'::text, 'internal'::text, 'audit'::text])))
);

CREATE TABLE runtime_legal_holds (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text,
    scope text NOT NULL,
    resource_id text NOT NULL,
    reason text NOT NULL,
    created_by_user_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    released_by_user_id text,
    released_at timestamp with time zone,
    CONSTRAINT runtime_legal_holds_scope_check CHECK ((scope = ANY (ARRAY['org'::text, 'user'::text, 'conversation'::text])))
);

CREATE TABLE runtime_memory_items (
    id text NOT NULL,
    scope_id text NOT NULL,
    org_id text NOT NULL,
    path text NOT NULL,
    content_ref text NOT NULL,
    content_summary text,
    checksum text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_by_run_id text,
    updated_by_run_id text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    encryption_version smallint DEFAULT 0 NOT NULL,
    retention_until timestamp with time zone,
    CONSTRAINT runtime_memory_items_version_check CHECK ((version > 0))
);

CREATE TABLE runtime_memory_scopes (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text,
    assistant_id text,
    scope_type text NOT NULL,
    namespace_hash text NOT NULL,
    namespace_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    policy_id text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_memory_scopes_scope_type_check CHECK ((scope_type = ANY (ARRAY['user'::text, 'organization'::text, 'assistant'::text, 'conversation'::text])))
);

CREATE TABLE runtime_model_call_usage (
    id text NOT NULL,
    org_id text NOT NULL,
    run_id text NOT NULL,
    conversation_id text NOT NULL,
    parent_event_id text,
    trace_id text NOT NULL,
    task_id text,
    subagent_id text,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cached_input_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    duration_ms integer DEFAULT 0 NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    cost_micro_usd bigint,
    pricing_id text,
    pricing_version text,
    created_at timestamp with time zone NOT NULL,
    connector_slug text,
    reasoning_tokens integer DEFAULT 0 NOT NULL,
    cache_creation_input_tokens integer DEFAULT 0 NOT NULL,
    audio_input_tokens integer DEFAULT 0 NOT NULL,
    audio_output_tokens integer DEFAULT 0 NOT NULL,
    purpose text DEFAULT 'main'::text NOT NULL,
    originating_tool_call_id text,
    originating_tool_name text,
    CONSTRAINT runtime_model_call_usage_cached_input_tokens_check CHECK ((cached_input_tokens >= 0)),
    CONSTRAINT runtime_model_call_usage_input_tokens_check CHECK ((input_tokens >= 0)),
    CONSTRAINT runtime_model_call_usage_output_tokens_check CHECK ((output_tokens >= 0)),
    CONSTRAINT runtime_model_call_usage_total_tokens_check CHECK ((total_tokens >= 0))
);

CREATE TABLE runtime_outbox_events (
    id text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    org_id text NOT NULL,
    event_type text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone NOT NULL,
    locked_by text,
    lock_expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_outbox_events_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT runtime_outbox_events_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'completed'::text, 'retry'::text, 'dead_letter'::text])))
);

CREATE TABLE runtime_run_usage (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    conversation_id text NOT NULL,
    run_id text NOT NULL,
    assistant_id text,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cached_input_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    chunk_count integer DEFAULT 0 NOT NULL,
    first_token_ms integer,
    duration_ms integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    retention_until timestamp with time zone,
    pii_purged_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    cost_micro_usd bigint,
    pricing_id text,
    pricing_version text,
    reasoning_tokens bigint DEFAULT 0 NOT NULL,
    cache_creation_input_tokens bigint DEFAULT 0 NOT NULL,
    audio_input_tokens bigint DEFAULT 0 NOT NULL,
    audio_output_tokens bigint DEFAULT 0 NOT NULL,
    CONSTRAINT runtime_run_usage_cached_input_tokens_check CHECK ((cached_input_tokens >= 0)),
    CONSTRAINT runtime_run_usage_input_tokens_check CHECK ((input_tokens >= 0)),
    CONSTRAINT runtime_run_usage_output_tokens_check CHECK ((output_tokens >= 0)),
    CONSTRAINT runtime_run_usage_total_tokens_check CHECK ((total_tokens >= 0))
);

CREATE TABLE runtime_subagent_results (
    id text NOT NULL,
    task_id text NOT NULL,
    run_id text NOT NULL,
    org_id text NOT NULL,
    response_text text,
    execution_summary text,
    plan_summary text,
    artifacts_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    recent_messages_ref text,
    error_json jsonb,
    created_at timestamp with time zone NOT NULL,
    encryption_version smallint DEFAULT 0 NOT NULL
);

CREATE TABLE runtime_tool_budgets (
    id text NOT NULL,
    org_id text,
    tool_name text NOT NULL,
    max_calls_per_run integer NOT NULL,
    max_input_tokens_per_call integer,
    max_input_tokens_per_run integer,
    enforcement text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT runtime_tool_budgets_enforcement_check CHECK ((enforcement = ANY (ARRAY['soft'::text, 'hard'::text]))),
    CONSTRAINT runtime_tool_budgets_max_calls_per_run_check CHECK ((max_calls_per_run >= 1))
);

CREATE TABLE runtime_tool_invocations (
    id text NOT NULL,
    run_id text NOT NULL,
    task_id text,
    org_id text NOT NULL,
    tool_name text NOT NULL,
    connector_slug text,
    side_effect_class text NOT NULL,
    call_id text,
    status text NOT NULL,
    args_json_redacted jsonb DEFAULT '{}'::jsonb NOT NULL,
    result_summary_json_redacted jsonb DEFAULT '{}'::jsonb NOT NULL,
    approval_id text,
    external_ref text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    safe_error_code text,
    safe_error_message text,
    encryption_version smallint DEFAULT 0 NOT NULL,
    CONSTRAINT runtime_tool_invocations_side_effect_class_check CHECK ((side_effect_class = ANY (ARRAY['read'::text, 'write'::text, 'external_side_effect'::text, 'destructive'::text]))),
    CONSTRAINT runtime_tool_invocations_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'waiting_for_approval'::text, 'completed'::text, 'failed'::text, 'cancelled'::text])))
);

CREATE TABLE runtime_usage_daily_connector (
    org_id text NOT NULL,
    day date NOT NULL,
    connector_slug text NOT NULL,
    runs_count integer NOT NULL,
    distinct_users integer NOT NULL,
    input_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    cached_input_tokens bigint NOT NULL,
    total_tokens bigint NOT NULL,
    cost_micro_usd bigint,
    refreshed_at timestamp with time zone NOT NULL,
    model_name text DEFAULT ''::text NOT NULL
);

CREATE TABLE runtime_usage_daily_org (
    org_id text NOT NULL,
    day date NOT NULL,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    runs_count integer NOT NULL,
    distinct_users integer NOT NULL,
    input_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    cached_input_tokens bigint NOT NULL,
    total_tokens bigint NOT NULL,
    cost_micro_usd bigint,
    refreshed_at timestamp with time zone NOT NULL
);

CREATE TABLE runtime_usage_daily_purpose (
    org_id text NOT NULL,
    day date NOT NULL,
    purpose text NOT NULL,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    call_count integer NOT NULL,
    input_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    cached_input_tokens bigint NOT NULL,
    cache_creation_input_tokens bigint DEFAULT 0 NOT NULL,
    reasoning_tokens bigint DEFAULT 0 NOT NULL,
    audio_input_tokens bigint DEFAULT 0 NOT NULL,
    audio_output_tokens bigint DEFAULT 0 NOT NULL,
    total_tokens bigint NOT NULL,
    cost_micro_usd bigint,
    refreshed_at timestamp with time zone NOT NULL
);

CREATE TABLE runtime_usage_daily_subagent (
    org_id text NOT NULL,
    day date NOT NULL,
    subagent_slug text NOT NULL,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    call_count integer NOT NULL,
    input_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    cached_input_tokens bigint NOT NULL,
    cache_creation_input_tokens bigint DEFAULT 0 NOT NULL,
    reasoning_tokens bigint DEFAULT 0 NOT NULL,
    audio_input_tokens bigint DEFAULT 0 NOT NULL,
    audio_output_tokens bigint DEFAULT 0 NOT NULL,
    total_tokens bigint NOT NULL,
    cost_micro_usd bigint,
    refreshed_at timestamp with time zone NOT NULL
);

CREATE TABLE runtime_usage_daily_user (
    org_id text NOT NULL,
    user_id text NOT NULL,
    day date NOT NULL,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    runs_count integer NOT NULL,
    input_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    cached_input_tokens bigint NOT NULL,
    total_tokens bigint NOT NULL,
    cost_micro_usd bigint,
    refreshed_at timestamp with time zone NOT NULL
);

CREATE TABLE todo_extractions (
    id text NOT NULL,
    org_id text NOT NULL,
    owner_user_id text NOT NULL,
    run_id text NOT NULL,
    conversation_id text NOT NULL,
    proposed_text text NOT NULL,
    suggested_due text,
    suggested_project_id text,
    source_message_id text,
    confidence_score double precision DEFAULT 0.0 NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT todo_extractions_resolution_consistency CHECK ((((state = 'pending'::text) AND (resolved_at IS NULL)) OR ((state <> 'pending'::text) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT todo_extractions_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'accepted'::text, 'rejected'::text])))
);

CREATE TABLE usage_budget_reservations (
    reservation_id text NOT NULL,
    budget_id text NOT NULL,
    period_start date NOT NULL,
    run_id text NOT NULL,
    reserved_micro_usd bigint DEFAULT 0 NOT NULL,
    reserved_tokens bigint DEFAULT 0 NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone
);

CREATE TABLE usage_budget_state (
    budget_id text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    current_spend_micro_usd bigint DEFAULT 0 NOT NULL,
    current_spend_tokens bigint DEFAULT 0 NOT NULL,
    row_version integer DEFAULT 1 NOT NULL,
    last_charged_run_id text,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE usage_budgets (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text,
    scope text NOT NULL,
    period text NOT NULL,
    enforcement text NOT NULL,
    limit_micro_usd bigint,
    limit_tokens bigint,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    created_by_user_id text NOT NULL,
    CONSTRAINT usage_budgets_enforcement_check CHECK ((enforcement = ANY (ARRAY['soft'::text, 'hard'::text]))),
    CONSTRAINT usage_budgets_period_check CHECK ((period = ANY (ARRAY['day'::text, 'month'::text]))),
    CONSTRAINT usage_budgets_scope_check CHECK ((scope = ANY (ARRAY['org'::text, 'user'::text]))),
    CONSTRAINT usage_budgets_status_check CHECK ((status = ANY (ARRAY['active'::text, 'disabled'::text])))
);

CREATE TABLE workspace_defaults (
    org_id text NOT NULL,
    default_model jsonb DEFAULT '{}'::jsonb NOT NULL,
    default_connectors jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id text,
    behavior_overrides jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled_models jsonb
);

ALTER TABLE ONLY agent_conversation_tool_ordinals
    ADD CONSTRAINT agent_conversation_tool_ordina_conversation_id_tool_call_id_key UNIQUE (conversation_id, tool_call_id);

ALTER TABLE ONLY agent_conversation_tool_ordinals
    ADD CONSTRAINT agent_conversation_tool_ordinals_pkey PRIMARY KEY (conversation_id, conversation_ordinal);

ALTER TABLE ONLY agent_conversations
    ADD CONSTRAINT agent_conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY conversation_share_recipients
    ADD CONSTRAINT conversation_share_recipients_pkey PRIMARY KEY (share_id, user_id);

ALTER TABLE ONLY conversation_shares
    ADD CONSTRAINT conversation_shares_pkey PRIMARY KEY (share_id);

ALTER TABLE ONLY model_pricing
    ADD CONSTRAINT model_pricing_pkey PRIMARY KEY (id);

ALTER TABLE ONLY retention_policies
    ADD CONSTRAINT retention_policies_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_approval_batch_items
    ADD CONSTRAINT runtime_approval_batch_items_batch_id_item_index_key UNIQUE (batch_id, item_index);

ALTER TABLE ONLY runtime_approval_batch_items
    ADD CONSTRAINT runtime_approval_batch_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_approval_batches
    ADD CONSTRAINT runtime_approval_batches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_async_tasks
    ADD CONSTRAINT runtime_async_tasks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_audit_log
    ADD CONSTRAINT runtime_audit_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_capability_snapshots
    ADD CONSTRAINT runtime_capability_snapshots_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_checkpoints
    ADD CONSTRAINT runtime_checkpoints_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_citations
    ADD CONSTRAINT runtime_citations_pkey PRIMARY KEY (run_id, citation_id);

ALTER TABLE ONLY runtime_compression_events
    ADD CONSTRAINT runtime_compression_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_consumer_cursors
    ADD CONSTRAINT runtime_consumer_cursors_pkey PRIMARY KEY (consumer_name, run_id);

ALTER TABLE ONLY runtime_context_payload_blobs
    ADD CONSTRAINT runtime_context_payload_blobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_context_payloads
    ADD CONSTRAINT runtime_context_payloads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_deletion_evidence
    ADD CONSTRAINT runtime_deletion_evidence_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_drafts
    ADD CONSTRAINT runtime_drafts_org_id_draft_id_version_key UNIQUE (org_id, draft_id, version);

ALTER TABLE ONLY runtime_drafts
    ADD CONSTRAINT runtime_drafts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_events
    ADD CONSTRAINT runtime_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_legal_holds
    ADD CONSTRAINT runtime_legal_holds_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_memory_items
    ADD CONSTRAINT runtime_memory_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_memory_scopes
    ADD CONSTRAINT runtime_memory_scopes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_model_call_usage
    ADD CONSTRAINT runtime_model_call_usage_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_outbox_events
    ADD CONSTRAINT runtime_outbox_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_run_usage
    ADD CONSTRAINT runtime_run_usage_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_run_usage
    ADD CONSTRAINT runtime_run_usage_run_id_key UNIQUE (run_id);

ALTER TABLE ONLY runtime_subagent_results
    ADD CONSTRAINT runtime_subagent_results_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_tool_budgets
    ADD CONSTRAINT runtime_tool_budgets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_tool_invocations
    ADD CONSTRAINT runtime_tool_invocations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY runtime_usage_daily_connector
    ADD CONSTRAINT runtime_usage_daily_connector_pkey PRIMARY KEY (org_id, day, connector_slug, model_name);

ALTER TABLE ONLY runtime_usage_daily_org
    ADD CONSTRAINT runtime_usage_daily_org_pkey PRIMARY KEY (org_id, day, model_provider, model_name);

ALTER TABLE ONLY runtime_usage_daily_purpose
    ADD CONSTRAINT runtime_usage_daily_purpose_pkey PRIMARY KEY (org_id, day, purpose, model_provider, model_name);

ALTER TABLE ONLY runtime_usage_daily_subagent
    ADD CONSTRAINT runtime_usage_daily_subagent_pkey PRIMARY KEY (org_id, day, subagent_slug, model_provider, model_name);

ALTER TABLE ONLY runtime_usage_daily_user
    ADD CONSTRAINT runtime_usage_daily_user_pkey PRIMARY KEY (org_id, user_id, day, model_provider, model_name);

ALTER TABLE ONLY todo_extractions
    ADD CONSTRAINT todo_extractions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY usage_budget_reservations
    ADD CONSTRAINT usage_budget_reservations_pkey PRIMARY KEY (reservation_id);

ALTER TABLE ONLY usage_budget_state
    ADD CONSTRAINT usage_budget_state_pkey PRIMARY KEY (budget_id, period_start);

ALTER TABLE ONLY usage_budgets
    ADD CONSTRAINT usage_budgets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY workspace_defaults
    ADD CONSTRAINT workspace_defaults_pkey PRIMARY KEY (org_id);

CREATE INDEX idx_actio_conversation_run ON agent_conversation_tool_ordinals USING btree (conversation_id, run_id);

CREATE INDEX idx_agent_conversations_enabled_connectors ON agent_conversations USING gin (enabled_connectors jsonb_path_ops);

CREATE INDEX idx_agent_conversations_folder ON agent_conversations USING btree (org_id, user_id, folder, updated_at DESC) WHERE ((folder IS NOT NULL) AND (deleted_at IS NULL));

CREATE INDEX idx_agent_conversations_forked_from_message ON agent_conversations USING btree (forked_from_message_id) WHERE (forked_from_message_id IS NOT NULL);

CREATE UNIQUE INDEX idx_agent_conversations_idempotency ON agent_conversations USING btree (org_id, user_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE INDEX idx_agent_conversations_org_status_updated ON agent_conversations USING btree (org_id, status, updated_at);

CREATE INDEX idx_agent_conversations_org_user_active_updated ON agent_conversations USING btree (org_id, user_id, updated_at DESC) WHERE (deleted_at IS NULL);

CREATE INDEX idx_agent_conversations_org_user_pinned_updated ON agent_conversations USING btree (org_id, user_id, updated_at DESC) WHERE (pinned AND (deleted_at IS NULL));

CREATE INDEX idx_agent_conversations_org_user_updated ON agent_conversations USING btree (org_id, user_id, updated_at);

CREATE INDEX idx_agent_conversations_parent ON agent_conversations USING btree (parent_conversation_id) WHERE (parent_conversation_id IS NOT NULL);

CREATE INDEX idx_agent_messages_org_conversation_created ON agent_messages USING btree (org_id, conversation_id, created_at);

CREATE INDEX idx_agent_messages_org_run ON agent_messages USING btree (org_id, run_id);

CREATE INDEX idx_agent_messages_retention_until ON agent_messages USING btree (org_id, retention_until) WHERE (retention_until IS NOT NULL);

CREATE UNIQUE INDEX idx_agent_runs_idempotency ON agent_runs USING btree (org_id, user_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE INDEX idx_agent_runs_org_conversation_created ON agent_runs USING btree (org_id, conversation_id, created_at);

CREATE INDEX idx_agent_runs_org_status_started ON agent_runs USING btree (org_id, status, started_at);

CREATE UNIQUE INDEX idx_model_pricing_active ON model_pricing USING btree (provider, model_name, region) WHERE (effective_until IS NULL);

CREATE INDEX idx_model_pricing_lookup ON model_pricing USING btree (provider, model_name, region, effective_from DESC);

CREATE INDEX idx_retention_policies_org_kind ON retention_policies USING btree (org_id, kind);

CREATE UNIQUE INDEX idx_retention_policies_unique ON retention_policies USING btree (org_id, scope, COALESCE(resource_id, ''::text), kind);

CREATE INDEX idx_runtime_approval_batch_items_batch ON runtime_approval_batch_items USING btree (batch_id);

CREATE INDEX idx_runtime_approval_batches_org_run ON runtime_approval_batches USING btree (org_id, run_id);

CREATE INDEX idx_runtime_approval_batches_status_expires ON runtime_approval_batches USING btree (status, expires_at) WHERE (expires_at IS NOT NULL);

CREATE INDEX idx_runtime_approval_requests_chain_parent ON runtime_approval_requests USING btree (run_id, chain_parent_approval_id) WHERE (chain_parent_approval_id IS NOT NULL);

CREATE INDEX idx_runtime_approval_requests_org_run_status ON runtime_approval_requests USING btree (org_id, run_id, status);

CREATE INDEX idx_runtime_approval_requests_org_user_status_created ON runtime_approval_requests USING btree (org_id, requested_by_user_id, status, created_at);

CREATE INDEX idx_runtime_async_tasks_org_run_status ON runtime_async_tasks USING btree (org_id, run_id, status);

CREATE INDEX idx_runtime_async_tasks_org_subagent_status ON runtime_async_tasks USING btree (org_id, subagent_name, status);

CREATE INDEX idx_runtime_async_tasks_org_thread ON runtime_async_tasks USING btree (org_id, thread_id);

CREATE INDEX idx_runtime_audit_log_org_resource ON runtime_audit_log USING btree (org_id, resource_type, resource_id);

CREATE INDEX idx_runtime_audit_log_org_run_created ON runtime_audit_log USING btree (org_id, run_id, created_at);

CREATE INDEX idx_runtime_audit_log_org_seq ON runtime_audit_log USING btree (org_id, seq);

CREATE INDEX idx_runtime_audit_log_org_trace ON runtime_audit_log USING btree (org_id, trace_id);

CREATE INDEX idx_runtime_audit_log_org_user_created ON runtime_audit_log USING btree (org_id, user_id, created_at);

CREATE INDEX idx_runtime_capability_snapshots_org_name ON runtime_capability_snapshots USING btree (org_id, capability_name);

CREATE INDEX idx_runtime_capability_snapshots_org_run_type ON runtime_capability_snapshots USING btree (org_id, run_id, capability_type);

CREATE INDEX idx_runtime_checkpoints_org_thread_created ON runtime_checkpoints USING btree (org_id, thread_id, created_at);

CREATE UNIQUE INDEX idx_runtime_checkpoints_org_thread_namespace_version ON runtime_checkpoints USING btree (org_id, thread_id, checkpoint_namespace, checkpoint_version);

CREATE INDEX idx_runtime_compression_events_org_run_created ON runtime_compression_events USING btree (org_id, run_id, created_at);

CREATE INDEX idx_runtime_context_payload_blobs_org ON runtime_context_payload_blobs USING btree (org_id);

CREATE INDEX idx_runtime_context_payload_blobs_payload ON runtime_context_payload_blobs USING btree (payload_id);

CREATE INDEX idx_runtime_context_payloads_org_retention ON runtime_context_payloads USING btree (org_id, retention_until);

CREATE INDEX idx_runtime_context_payloads_org_run_created ON runtime_context_payloads USING btree (org_id, run_id, created_at);

CREATE INDEX idx_runtime_deletion_evidence_org_user_created ON runtime_deletion_evidence USING btree (org_id, user_id, created_at);

CREATE INDEX idx_runtime_events_org_conversation_created ON runtime_events USING btree (org_id, conversation_id, created_at);

CREATE INDEX idx_runtime_events_org_run_sequence ON runtime_events USING btree (org_id, run_id, sequence_no);

CREATE INDEX idx_runtime_events_org_trace ON runtime_events USING btree (org_id, trace_id);

CREATE INDEX idx_runtime_events_retention_until ON runtime_events USING btree (org_id, retention_until) WHERE (retention_until IS NOT NULL);

CREATE UNIQUE INDEX idx_runtime_events_run_sequence ON runtime_events USING btree (run_id, sequence_no);

CREATE INDEX idx_runtime_legal_holds_org_resource_active ON runtime_legal_holds USING btree (org_id, scope, resource_id) WHERE (released_at IS NULL);

CREATE INDEX idx_runtime_memory_items_org_scope_updated ON runtime_memory_items USING btree (org_id, scope_id, updated_at);

CREATE INDEX idx_runtime_memory_items_retention_until ON runtime_memory_items USING btree (org_id, retention_until) WHERE (retention_until IS NOT NULL);

CREATE UNIQUE INDEX idx_runtime_memory_items_scope_path_active ON runtime_memory_items USING btree (scope_id, path) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX idx_runtime_memory_scopes_org_scope_namespace ON runtime_memory_scopes USING btree (org_id, scope_type, namespace_hash);

CREATE INDEX idx_runtime_memory_scopes_org_user_scope ON runtime_memory_scopes USING btree (org_id, user_id, scope_type);

CREATE INDEX idx_runtime_model_call_usage_org_connector_created ON runtime_model_call_usage USING btree (org_id, connector_slug, created_at) WHERE (connector_slug IS NOT NULL);

CREATE INDEX idx_runtime_model_call_usage_org_run ON runtime_model_call_usage USING btree (org_id, run_id, created_at);

CREATE INDEX idx_runtime_model_call_usage_org_task ON runtime_model_call_usage USING btree (org_id, task_id) WHERE (task_id IS NOT NULL);

CREATE INDEX idx_runtime_model_call_usage_org_trace ON runtime_model_call_usage USING btree (org_id, trace_id);

CREATE INDEX idx_runtime_outbox_locked ON runtime_outbox_events USING btree (locked_by, lock_expires_at);

CREATE INDEX idx_runtime_outbox_org_aggregate ON runtime_outbox_events USING btree (org_id, aggregate_type, aggregate_id);

CREATE INDEX idx_runtime_outbox_status_available ON runtime_outbox_events USING btree (status, available_at);

CREATE INDEX idx_runtime_run_usage_org_completed ON runtime_run_usage USING btree (org_id, completed_at DESC);

CREATE INDEX idx_runtime_run_usage_org_conversation_completed ON runtime_run_usage USING btree (org_id, conversation_id, completed_at DESC);

CREATE INDEX idx_runtime_run_usage_org_model_completed ON runtime_run_usage USING btree (org_id, model_provider, model_name, completed_at DESC);

CREATE INDEX idx_runtime_run_usage_org_user_completed ON runtime_run_usage USING btree (org_id, user_id, completed_at DESC);

CREATE INDEX idx_runtime_run_usage_retention ON runtime_run_usage USING btree (retention_until) WHERE (pii_purged_at IS NULL);

CREATE INDEX idx_runtime_subagent_results_run_created ON runtime_subagent_results USING btree (run_id, created_at);

CREATE UNIQUE INDEX idx_runtime_subagent_results_task ON runtime_subagent_results USING btree (task_id);

CREATE INDEX idx_runtime_tool_budgets_org ON runtime_tool_budgets USING btree (org_id);

CREATE INDEX idx_runtime_tool_invocations_org_connector_tool ON runtime_tool_invocations USING btree (org_id, connector_slug, tool_name);

CREATE INDEX idx_runtime_tool_invocations_org_run_started ON runtime_tool_invocations USING btree (org_id, run_id, started_at);

CREATE UNIQUE INDEX idx_runtime_tool_invocations_run_call ON runtime_tool_invocations USING btree (run_id, call_id) WHERE (call_id IS NOT NULL);

CREATE INDEX idx_runtime_usage_daily_connector_org_day ON runtime_usage_daily_connector USING btree (org_id, day DESC);

CREATE INDEX idx_runtime_usage_daily_org_day ON runtime_usage_daily_org USING btree (org_id, day DESC);

CREATE INDEX idx_runtime_usage_daily_purpose_org_day ON runtime_usage_daily_purpose USING btree (org_id, day DESC);

CREATE INDEX idx_runtime_usage_daily_subagent_org_day ON runtime_usage_daily_subagent USING btree (org_id, day DESC);

CREATE INDEX idx_runtime_usage_daily_user_org_day ON runtime_usage_daily_user USING btree (org_id, day DESC);

CREATE INDEX idx_usage_budget_reservations_active ON usage_budget_reservations USING btree (budget_id, period_start) WHERE (consumed_at IS NULL);

CREATE INDEX idx_usage_budget_reservations_expiring ON usage_budget_reservations USING btree (expires_at) WHERE (consumed_at IS NULL);

CREATE INDEX idx_usage_budgets_org_status ON usage_budgets USING btree (org_id, status);

CREATE INDEX ix_conversation_share_recipients_user ON conversation_share_recipients USING btree (user_id);

CREATE INDEX ix_conversation_shares_active ON conversation_shares USING btree (org_id, conversation_id, created_at DESC) WHERE (revoked_at IS NULL);

CREATE INDEX ix_todo_extractions_org_run ON todo_extractions USING btree (org_id, run_id);

CREATE INDEX ix_todo_extractions_owner_pending ON todo_extractions USING btree (org_id, owner_user_id, created_at DESC) WHERE (state = 'pending'::text);

CREATE INDEX runtime_citations_conv_idx ON runtime_citations USING btree (conversation_id, created_at);

CREATE INDEX runtime_citations_org_idx ON runtime_citations USING btree (org_id);

CREATE UNIQUE INDEX runtime_citations_run_source_uk ON runtime_citations USING btree (run_id, source_connector, source_doc_id);

CREATE INDEX runtime_drafts_conversation_idx ON runtime_drafts USING btree (org_id, conversation_id, draft_id, version DESC);

CREATE INDEX runtime_drafts_draft_id_version_idx ON runtime_drafts USING btree (org_id, draft_id, version DESC);

CREATE UNIQUE INDEX uq_runtime_tool_budgets_scope ON runtime_tool_budgets USING btree (COALESCE(org_id, '<global>'::text), tool_name);

CREATE UNIQUE INDEX uq_usage_budget_reservations_run ON usage_budget_reservations USING btree (budget_id, run_id) WHERE (consumed_at IS NULL);

CREATE UNIQUE INDEX uq_usage_budgets_scope ON usage_budgets USING btree (org_id, COALESCE(user_id, '<org>'::text), scope, period);

CREATE UNIQUE INDEX ux_conversation_shares_token_hash ON conversation_shares USING btree (share_token_hash) WHERE (share_token_hash IS NOT NULL);

CREATE TRIGGER runtime_audit_log_immutable BEFORE DELETE OR UPDATE ON runtime_audit_log FOR EACH ROW EXECUTE FUNCTION runtime_audit_log_immutable_guard();

ALTER TABLE ONLY agent_conversation_tool_ordinals
    ADD CONSTRAINT agent_conversation_tool_ordinals_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY agent_messages
    ADD CONSTRAINT agent_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id);

ALTER TABLE ONLY agent_messages
    ADD CONSTRAINT agent_messages_parent_message_id_fkey FOREIGN KEY (parent_message_id) REFERENCES agent_messages(id);

ALTER TABLE ONLY agent_runs
    ADD CONSTRAINT agent_runs_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id);

ALTER TABLE ONLY agent_runs
    ADD CONSTRAINT agent_runs_user_message_id_fkey FOREIGN KEY (user_message_id) REFERENCES agent_messages(id);

ALTER TABLE ONLY conversation_share_recipients
    ADD CONSTRAINT conversation_share_recipients_share_id_fkey FOREIGN KEY (share_id) REFERENCES conversation_shares(share_id) ON DELETE CASCADE;

ALTER TABLE ONLY conversation_shares
    ADD CONSTRAINT conversation_shares_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY agent_conversations
    ADD CONSTRAINT fk_agent_conversations_parent FOREIGN KEY (parent_conversation_id) REFERENCES agent_conversations(id) ON DELETE SET NULL;

ALTER TABLE ONLY agent_messages
    ADD CONSTRAINT fk_agent_messages_run FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_tool_invocations
    ADD CONSTRAINT fk_runtime_tool_invocations_approval FOREIGN KEY (approval_id) REFERENCES runtime_approval_requests(id);

ALTER TABLE ONLY runtime_approval_batch_items
    ADD CONSTRAINT runtime_approval_batch_items_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES runtime_approval_batches(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_approval_batches
    ADD CONSTRAINT runtime_approval_batches_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_chain_parent_approval_id_fkey FOREIGN KEY (chain_parent_approval_id) REFERENCES runtime_approval_requests(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_tool_invocation_id_fkey FOREIGN KEY (tool_invocation_id) REFERENCES runtime_tool_invocations(id);

ALTER TABLE ONLY runtime_async_tasks
    ADD CONSTRAINT runtime_async_tasks_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id);

ALTER TABLE ONLY runtime_async_tasks
    ADD CONSTRAINT runtime_async_tasks_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_audit_log
    ADD CONSTRAINT runtime_audit_log_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_capability_snapshots
    ADD CONSTRAINT runtime_capability_snapshots_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_citations
    ADD CONSTRAINT runtime_citations_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_citations
    ADD CONSTRAINT runtime_citations_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_compression_events
    ADD CONSTRAINT runtime_compression_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_consumer_cursors
    ADD CONSTRAINT runtime_consumer_cursors_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_context_payload_blobs
    ADD CONSTRAINT runtime_context_payload_blobs_payload_id_fkey FOREIGN KEY (payload_id) REFERENCES runtime_context_payloads(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_context_payloads
    ADD CONSTRAINT runtime_context_payloads_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_context_payloads
    ADD CONSTRAINT runtime_context_payloads_task_id_fkey FOREIGN KEY (task_id) REFERENCES runtime_async_tasks(id);

ALTER TABLE ONLY runtime_context_payloads
    ADD CONSTRAINT runtime_context_payloads_tool_invocation_id_fkey FOREIGN KEY (tool_invocation_id) REFERENCES runtime_tool_invocations(id);

ALTER TABLE ONLY runtime_deletion_evidence
    ADD CONSTRAINT runtime_deletion_evidence_audit_event_id_fkey FOREIGN KEY (audit_event_id) REFERENCES runtime_audit_log(id);

ALTER TABLE ONLY runtime_drafts
    ADD CONSTRAINT runtime_drafts_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY runtime_drafts
    ADD CONSTRAINT runtime_drafts_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY runtime_events
    ADD CONSTRAINT runtime_events_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id);

ALTER TABLE ONLY runtime_events
    ADD CONSTRAINT runtime_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_memory_items
    ADD CONSTRAINT runtime_memory_items_created_by_run_id_fkey FOREIGN KEY (created_by_run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_memory_items
    ADD CONSTRAINT runtime_memory_items_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES runtime_memory_scopes(id);

ALTER TABLE ONLY runtime_memory_items
    ADD CONSTRAINT runtime_memory_items_updated_by_run_id_fkey FOREIGN KEY (updated_by_run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_model_call_usage
    ADD CONSTRAINT runtime_model_call_usage_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id);

ALTER TABLE ONLY runtime_model_call_usage
    ADD CONSTRAINT runtime_model_call_usage_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_run_usage
    ADD CONSTRAINT runtime_run_usage_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent_conversations(id);

ALTER TABLE ONLY runtime_run_usage
    ADD CONSTRAINT runtime_run_usage_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_subagent_results
    ADD CONSTRAINT runtime_subagent_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_subagent_results
    ADD CONSTRAINT runtime_subagent_results_task_id_fkey FOREIGN KEY (task_id) REFERENCES runtime_async_tasks(id);

ALTER TABLE ONLY runtime_tool_invocations
    ADD CONSTRAINT runtime_tool_invocations_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent_runs(id);

ALTER TABLE ONLY runtime_tool_invocations
    ADD CONSTRAINT runtime_tool_invocations_task_id_fkey FOREIGN KEY (task_id) REFERENCES runtime_async_tasks(id);

ALTER TABLE ONLY usage_budget_reservations
    ADD CONSTRAINT usage_budget_reservations_budget_id_fkey FOREIGN KEY (budget_id) REFERENCES usage_budgets(id) ON DELETE CASCADE;

ALTER TABLE ONLY usage_budget_state
    ADD CONSTRAINT usage_budget_state_budget_id_fkey FOREIGN KEY (budget_id) REFERENCES usage_budgets(id) ON DELETE CASCADE;

ALTER TABLE agent_conversation_tool_ordinals ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime_approval_batch_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime_approval_batches ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime_usage_daily_connector ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime_usage_daily_purpose ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime_usage_daily_subagent ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON agent_conversation_tool_ordinals USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON agent_conversations USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON agent_messages USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON agent_runs USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON conversation_share_recipients USING ((EXISTS ( SELECT 1
   FROM conversation_shares s
  WHERE ((s.share_id = conversation_share_recipients.share_id) AND (s.org_id = current_setting('app.current_org_id'::text, true)))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM conversation_shares s
  WHERE ((s.share_id = conversation_share_recipients.share_id) AND (s.org_id = current_setting('app.current_org_id'::text, true))))));

CREATE POLICY tenant_isolation ON conversation_shares USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_approval_batch_items USING ((EXISTS ( SELECT 1
   FROM runtime_approval_batches b
  WHERE ((b.id = runtime_approval_batch_items.batch_id) AND (b.org_id = current_setting('app.current_org_id'::text, true)))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM runtime_approval_batches b
  WHERE ((b.id = runtime_approval_batch_items.batch_id) AND (b.org_id = current_setting('app.current_org_id'::text, true))))));

CREATE POLICY tenant_isolation ON runtime_approval_batches USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_approval_requests USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_async_tasks USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_audit_log USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_capability_snapshots USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_checkpoints USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_citations USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_compression_events USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_context_payloads USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_deletion_evidence USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_drafts USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_events USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_legal_holds USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_memory_items USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_memory_scopes USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_model_call_usage USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_run_usage USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_subagent_results USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_tool_invocations USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_usage_daily_connector USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON runtime_usage_daily_org USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON runtime_usage_daily_purpose USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON runtime_usage_daily_subagent USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON runtime_usage_daily_user USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON todo_extractions USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON usage_budgets USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON workspace_defaults USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_or_global ON runtime_tool_budgets USING (((org_id IS NULL) OR (current_setting('app.role'::text, true) = 'worker'::text) OR (org_id = current_setting('app.current_org_id'::text, true)))) WITH CHECK (((current_setting('app.role'::text, true) = 'worker'::text) OR (org_id = current_setting('app.current_org_id'::text, true))));

CREATE POLICY tenant_or_worker ON runtime_outbox_events USING (((current_setting('app.role'::text, true) = 'worker'::text) OR (org_id = current_setting('app.current_org_id'::text, true)))) WITH CHECK (((current_setting('app.role'::text, true) = 'worker'::text) OR (org_id = current_setting('app.current_org_id'::text, true))));

CREATE POLICY tenant_or_worker ON usage_budget_reservations USING (((current_setting('app.role'::text, true) = 'worker'::text) OR (EXISTS ( SELECT 1
   FROM usage_budgets b
  WHERE ((b.id = usage_budget_reservations.budget_id) AND (b.org_id = current_setting('app.current_org_id'::text, true))))))) WITH CHECK (((current_setting('app.role'::text, true) = 'worker'::text) OR (EXISTS ( SELECT 1
   FROM usage_budgets b
  WHERE ((b.id = usage_budget_reservations.budget_id) AND (b.org_id = current_setting('app.current_org_id'::text, true)))))));

CREATE POLICY tenant_or_worker ON usage_budget_state USING (((current_setting('app.role'::text, true) = 'worker'::text) OR (EXISTS ( SELECT 1
   FROM usage_budgets b
  WHERE ((b.id = usage_budget_state.budget_id) AND (b.org_id = current_setting('app.current_org_id'::text, true))))))) WITH CHECK (((current_setting('app.role'::text, true) = 'worker'::text) OR (EXISTS ( SELECT 1
   FROM usage_budgets b
  WHERE ((b.id = usage_budget_state.budget_id) AND (b.org_id = current_setting('app.current_org_id'::text, true)))))));

ALTER TABLE workspace_defaults ENABLE ROW LEVEL SECURITY;

-- ===================================================================
-- Role + privilege bootstrap. The migration history created these DB
-- roles and grants; pg_dump --no-privileges strips them, so they are
-- reproduced here from the migrated reference database (verified by
-- the catalog diff). Cluster-level roles: guarded for idempotency.
-- ===================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_writer') THEN
        CREATE ROLE audit_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'enterprise_admin') THEN
        CREATE ROLE enterprise_admin BYPASSRLS NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        CREATE ROLE enterprise_app NOINHERIT;
    END IF;
END
$$;

GRANT INSERT, SELECT ON runtime_audit_log TO audit_writer;
GRANT DELETE, INSERT, SELECT, UPDATE ON agent_conversations TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON agent_messages TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON agent_runs TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON conversation_share_recipients TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON conversation_shares TO enterprise_app;
GRANT SELECT ON model_pricing TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_approval_requests TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_async_tasks TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_audit_log TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_capability_snapshots TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_checkpoints TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_citations TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_compression_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_consumer_cursors TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_context_payloads TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_deletion_evidence TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_drafts TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_legal_holds TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_memory_items TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_memory_scopes TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_model_call_usage TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_outbox_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_run_usage TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_subagent_results TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_tool_budgets TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_tool_invocations TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_usage_daily_org TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON runtime_usage_daily_user TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON todo_extractions TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON usage_budget_reservations TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON usage_budget_state TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON usage_budgets TO enterprise_app;

-- ===== Bootstrap data: global default tool budget (from 0010) =====
INSERT INTO runtime_tool_budgets (
    id, org_id, tool_name, max_calls_per_run, enforcement, created_at, updated_at
) VALUES (
    'seed_default', NULL, '*', 6, 'hard', now(), now()
) ON CONFLICT DO NOTHING;
