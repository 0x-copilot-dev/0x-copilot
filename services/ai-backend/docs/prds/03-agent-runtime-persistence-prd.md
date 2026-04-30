# PRD: Agent Runtime Persistence

## Purpose

Define durable persistence requirements for the FastAPI runtime API, including database choice, modular storage ports, table responsibilities, columns, indexes, retention, encryption, migrations, and anti-patterns to avoid.

This PRD is documentation-only. Implementation comes in later rounds.

## Database Recommendation

Use PostgreSQL as the default primary database.

PostgreSQL is the right first database because this runtime state is relational, multi-tenant, transactional, and audit-heavy. Conversations, messages, runs, events, subagent tasks, tool calls, approvals, memory metadata, and audit records have strong relationships and invariants that should be enforced with constraints, indexes, and transactions.

Supabase is acceptable as a managed PostgreSQL provider for development, staging, or production if its operational and enterprise requirements fit. The runtime must target portable PostgreSQL and standard SQL migrations, not Supabase-only SDK behavior. This keeps the design compatible with Supabase, AWS RDS, Cloud SQL, Azure Database for PostgreSQL, Neon, Crunchy, or self-managed PostgreSQL.

## Problem

The runtime needs to load conversation history whenever the user sends input and must persist every meaningful state transition. Without a durable schema, the system cannot safely support reconnect, replay, cancellation, audit, approvals, subagents, memory references, or worker recovery.

The design must avoid the common anti-pattern of storing all agent state as one large JSON document. Some payloads are flexible, but the core workflow has clear entities that deserve relational tables.

## Goals

- Store conversation history and runtime state durably.
- Support replayable ordered stream events.
- Store async subagent task state outside message history.
- Track tool invocations, approvals, memory metadata, context payload refs, and audit records.
- Keep large payloads by reference instead of inline in messages or events.
- Support modular persistence providers through typed ports.
- Make PostgreSQL the default while remaining Supabase-compatible and provider-portable.
- Provide enough schema detail for future migrations and implementation PRDs.

## Non-Goals

- Implement migrations in this round.
- Select the final cloud provider.
- Define full tenant/auth source-of-truth tables.
- Store raw connector payloads or secrets in the primary runtime tables.
- Replace vector search, object storage, or connector-owned systems with runtime tables.

## Storage Architecture

The implementation should define ports before concrete adapters:

- `PersistencePort`: conversation, message, run, approval, audit, and metadata operations.
- `EventStorePort`: append and replay ordered runtime events.
- `RuntimeQueuePort`: enqueue, claim, retry, and complete runtime commands.
- `MemoryMetadataPort`: memory scope and item metadata.
- `PayloadStoragePort`: write and read large offloaded payloads by reference.
- `CheckpointStorePort`: LangGraph/runtime checkpoint metadata and blob references.

PostgreSQL should back the first implementations for `PersistencePort`, `EventStorePort`, `RuntimeQueuePort`, `MemoryMetadataPort`, and checkpoint metadata. Object storage should back large payload blobs later.

## Schema Rules

- Every tenant-scoped table includes `org_id`.
- Every user-owned row includes `user_id` where ownership matters.
- Primary IDs should be stable UUIDs or ULIDs.
- Use `created_at` and `updated_at` where rows mutate.
- Use terminal timestamps such as `completed_at`, `cancelled_at`, or `deleted_at` where state transitions matter.
- Use foreign keys for core workflow relationships.
- Use JSONB for typed payloads, redacted metadata, snapshots, and provider-specific options only.
- Do not store unredacted secrets in JSONB.
- Do not store large connector payloads inline; use `runtime_context_payloads`.
- Do not mutate emitted `runtime_events`.
- Prefer append-only audit records over mutable audit state.
- Add `schema_version` or protocol version fields where clients or migrations need compatibility.

## Core Tables

### `agent_conversations`

Stores conversation shells and current lifecycle state.

Columns:

- `id`
- `org_id`
- `user_id`
- `assistant_id`
- `title`
- `status`
- `created_at`
- `updated_at`
- `archived_at`
- `metadata_json`
- `schema_version`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, user_id, updated_at)`.
- Index on `(org_id, status, updated_at)`.
- Check constraint for known statuses.

### `agent_messages`

Stores ordered conversation messages. Messages should contain user-visible content or safe summaries, not raw oversized payloads.

Columns:

- `id`
- `conversation_id`
- `org_id`
- `run_id`
- `role`
- `content_text`
- `content_format`
- `parent_message_id`
- `token_count`
- `trace_id`
- `status`
- `created_at`
- `edited_at`
- `deleted_at`

Indexes and constraints:

- Primary key on `id`.
- Foreign key to `agent_conversations.id`.
- Nullable foreign key to `agent_runs.id`.
- Index on `(org_id, conversation_id, created_at)`.
- Index on `(org_id, run_id)`.
- Check constraint for known roles and statuses.

### `agent_runs`

Stores one runtime execution for one user input.

Columns:

- `id`
- `conversation_id`
- `org_id`
- `user_id`
- `user_message_id`
- `idempotency_key`
- `trace_id`
- `status`
- `model_provider`
- `model_name`
- `model_config_json`
- `runtime_version`
- `request_options_json`
- `started_at`
- `completed_at`
- `cancelled_at`
- `safe_error_code`
- `safe_error_message`
- `row_version`

Indexes and constraints:

- Primary key on `id`.
- Foreign key to `agent_conversations.id`.
- Foreign key to `agent_messages.id` for `user_message_id`.
- Unique index on `(org_id, user_id, idempotency_key)` when `idempotency_key` is present.
- Index on `(org_id, conversation_id, created_at)`.
- Index on `(org_id, status, started_at)`.
- Check constraint for known run states.

### `runtime_events`

Stores append-only, replayable, client-visible runtime events.

Columns:

- `id`
- `run_id`
- `conversation_id`
- `org_id`
- `sequence_no`
- `event_protocol_version`
- `source`
- `event_type`
- `parent_task_id`
- `trace_id`
- `payload_json_redacted`
- `metadata_json_redacted`
- `visibility`
- `created_at`

Indexes and constraints:

- Primary key on `id`.
- Unique index on `(run_id, sequence_no)`.
- Index on `(org_id, run_id, sequence_no)`.
- Index on `(org_id, conversation_id, created_at)`.
- Index on `(org_id, trace_id)`.
- Check constraint for known visibility classes.

### `runtime_outbox_events`

Stores durable commands and integration events for producer/consumer processing.

Columns:

- `id`
- `aggregate_type`
- `aggregate_id`
- `org_id`
- `event_type`
- `payload_json`
- `status`
- `attempts`
- `available_at`
- `locked_by`
- `lock_expires_at`
- `created_at`
- `updated_at`

Indexes and constraints:

- Primary key on `id`.
- Index on `(status, available_at)`.
- Index on `(locked_by, lock_expires_at)`.
- Index on `(org_id, aggregate_type, aggregate_id)`.
- Check constraint for known statuses.

### `runtime_consumer_cursors`

Tracks durable consumers when a consumer needs its own replay position.

Columns:

- `consumer_name`
- `run_id`
- `last_sequence_no`
- `last_event_id`
- `updated_at`

Indexes and constraints:

- Primary key on `(consumer_name, run_id)`.
- Foreign key to `agent_runs.id`.

### `runtime_async_tasks`

Stores async subagent metadata outside message history.

Columns:

- `id`
- `run_id`
- `conversation_id`
- `org_id`
- `parent_task_id`
- `subagent_name`
- `thread_id`
- `langgraph_run_id`
- `status`
- `objective_summary`
- `constraints_json`
- `output_contract_json`
- `timeout_seconds`
- `started_at`
- `updated_at`
- `completed_at`
- `cancelled_at`
- `safe_error_code`
- `safe_error_message`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, run_id, status)`.
- Index on `(org_id, subagent_name, status)`.
- Index on `(org_id, thread_id)`.
- Check constraint for known task states.

### `runtime_subagent_results`

Stores subagent outputs and summaries.

Columns:

- `id`
- `task_id`
- `run_id`
- `response_text`
- `execution_summary`
- `plan_summary`
- `artifacts_json`
- `recent_messages_ref`
- `error_json`
- `created_at`

Indexes and constraints:

- Primary key on `id`.
- Unique index on `task_id`.
- Index on `(run_id, created_at)`.

### `runtime_tool_invocations`

Stores tool and connector invocation state with redacted inputs and outputs.

Columns:

- `id`
- `run_id`
- `task_id`
- `org_id`
- `tool_name`
- `connector_slug`
- `side_effect_class`
- `call_id`
- `status`
- `args_json_redacted`
- `result_summary_json_redacted`
- `approval_id`
- `external_ref`
- `started_at`
- `completed_at`
- `safe_error_code`
- `safe_error_message`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, run_id, started_at)`.
- Index on `(org_id, connector_slug, tool_name)`.
- Unique index on `(run_id, call_id)` where `call_id` is present.
- Check constraint for side-effect class and status.

### `runtime_approval_requests`

Stores approval requests for side-effecting actions.

Columns:

- `id`
- `run_id`
- `tool_invocation_id`
- `org_id`
- `requested_by_user_id`
- `status`
- `risk_class`
- `action_summary`
- `request_payload_json_redacted`
- `decided_by_user_id`
- `decision_reason`
- `expires_at`
- `created_at`
- `decided_at`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, requested_by_user_id, status, created_at)`.
- Index on `(org_id, run_id, status)`.
- Check constraint for approval status and risk class.

### `runtime_memory_scopes`

Stores scoped memory namespaces.

Columns:

- `id`
- `org_id`
- `user_id`
- `assistant_id`
- `scope_type`
- `namespace_hash`
- `namespace_json`
- `policy_id`
- `created_at`
- `updated_at`

Indexes and constraints:

- Primary key on `id`.
- Unique index on `(org_id, scope_type, namespace_hash)`.
- Index on `(org_id, user_id, scope_type)`.
- Check constraint for known scope types.

### `runtime_memory_items`

Stores memory item metadata and content references.

Columns:

- `id`
- `scope_id`
- `org_id`
- `path`
- `content_ref`
- `content_summary`
- `checksum`
- `version`
- `created_by_run_id`
- `updated_by_run_id`
- `created_at`
- `updated_at`
- `deleted_at`

Indexes and constraints:

- Primary key on `id`.
- Unique index on `(scope_id, path)` for active rows.
- Index on `(org_id, scope_id, updated_at)`.
- Version column for optimistic concurrency.

### `runtime_context_payloads`

Stores references to large payloads and offloaded content.

Columns:

- `id`
- `run_id`
- `task_id`
- `tool_invocation_id`
- `org_id`
- `kind`
- `storage_backend`
- `storage_uri`
- `sha256`
- `byte_size`
- `mime_type`
- `redaction_state`
- `retention_until`
- `created_at`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, run_id, created_at)`.
- Index on `(org_id, retention_until)`.
- Check constraint for known redaction states.

### `runtime_compression_events`

Stores redacted context compression telemetry.

Columns:

- `id`
- `run_id`
- `org_id`
- `before_tokens`
- `after_tokens`
- `strategy`
- `payload_refs_json`
- `trace_id`
- `created_at`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, run_id, created_at)`.
- Check constraint that token counts are non-negative.

### `runtime_capability_snapshots`

Stores model-visible capability summaries available during a run.

Columns:

- `id`
- `run_id`
- `org_id`
- `capability_type`
- `capability_name`
- `capability_version`
- `scopes_json`
- `risk_class`
- `summary`
- `loaded_at`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, run_id, capability_type)`.
- Index on `(org_id, capability_name)`.

### `runtime_audit_log`

Stores append-only security and operational audit events.

Columns:

- `id`
- `org_id`
- `user_id`
- `actor_type`
- `action`
- `resource_type`
- `resource_id`
- `run_id`
- `trace_id`
- `outcome`
- `metadata_json_redacted`
- `created_at`

Indexes and constraints:

- Primary key on `id`.
- Index on `(org_id, user_id, created_at)`.
- Index on `(org_id, resource_type, resource_id)`.
- Index on `(org_id, run_id, created_at)`.
- Index on `(org_id, trace_id)`.

### `runtime_checkpoints`

Stores runtime checkpoint metadata and blob references.

Columns:

- `id`
- `org_id`
- `thread_id`
- `checkpoint_namespace`
- `checkpoint_version`
- `checkpoint_blob_ref`
- `metadata_json`
- `created_at`

Indexes and constraints:

- Primary key on `id`.
- Unique index on `(org_id, thread_id, checkpoint_namespace, checkpoint_version)`.
- Index on `(org_id, thread_id, created_at)`.

## Loading Conversation History

When the user sends input, the API should:

1. Validate tenant/user/run request context.
2. Load `agent_conversations` by `conversation_id` and `org_id`.
3. Load recent active `agent_messages` in chronological order.
4. Load relevant memory metadata through memory policy, not by scanning all memory rows.
5. Load any unresolved approval or active run state that affects whether a new run is allowed.
6. Create a new user message and run in one transaction.
7. Enqueue the run command only after the transaction commits, or through an outbox record committed with the run.

## Retention

Default retention should be configurable by organization and environment:

- Conversations and messages: product-defined retention.
- Runtime events: retain long enough for audit and debugging; allow compaction after final response if policy permits.
- Context payloads: shorter retention for large payloads, with `retention_until`.
- Audit logs: enterprise retention, usually longer than stream events.
- Checkpoints: retain only while needed for resumability and debugging.

## Encryption And Redaction

- Use encryption at rest from the database provider.
- Support application-level encryption later for sensitive payload refs if required.
- Redact before inserting into `runtime_events`, `runtime_tool_invocations`, `runtime_audit_log`, and logs.
- Never store provider API keys, OAuth tokens, session cookies, or raw credentials in runtime tables.
- Store object storage URIs as references, not public URLs.

## Migration Requirements

- Use a migration tool with deterministic ordered migrations.
- Every migration must be reversible where practical.
- Migrations that change event or API shape must account for `schema_version` or `event_protocol_version`.
- CI must validate migrations against an empty database and a representative existing schema.
- Backfills must be explicit and safe for large tenant tables.

## Anti-Patterns To Avoid

- One generic `agent_state` JSON table for all runtime state.
- Storing full connector payloads inline in messages or events.
- Using Redis as the durable source of truth.
- Depending on Supabase-only client SDK behavior in domain code.
- Using vector storage as the source of truth for conversations or audit.
- Mutating emitted stream events to fix client presentation.
- Allowing subagent state to exist only inside message text.
- Skipping `org_id` because the first environment is single-tenant.

## Acceptance Criteria

- The persistence design names PostgreSQL as the primary database and documents Supabase as a compatible managed option.
- The schema covers conversations, messages, runs, events, outbox, consumer cursors, async tasks, subagent results, tool invocations, approvals, memory, payload refs, compression events, capability snapshots, audit, and checkpoints.
- Each table has a clear responsibility, columns, and key indexes or constraints.
- The design keeps large payloads and unredacted secrets out of primary runtime tables.
- Database access stays behind modular ports so providers can change without changing runtime contracts.
