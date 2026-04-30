# Spec: Agent Runtime Persistence

## Purpose

Document the implemented persistence contracts and PostgreSQL schema for durable agent runtime state.

PostgreSQL is the first durable database target. Supabase is acceptable as a managed PostgreSQL provider when it satisfies the same SQL schema, migration, pooling, backup, and network requirements. Domain code must target portable PostgreSQL contracts and avoid Supabase-only SDK behavior.

## Implemented Modules

- `src/agent_runtime/persistence/records/`: grouped Pydantic records for durable runtime entities.
- `src/agent_runtime/persistence/contracts.py`: compatibility re-export for persistence records.
- `src/agent_runtime/persistence/constants.py`: stable field names, event names, validation patterns, and defaults.
- `src/agent_runtime/persistence/ports.py`: future payload and checkpoint storage protocols.
- `src/agent_runtime/persistence/schema/postgres.py`: initial PostgreSQL migration catalog and table list.
- `src/agent_runtime/persistence/postgres/schema.py`: compatibility re-export for the PostgreSQL schema catalog.
- `src/runtime_adapters/in_memory/`: in-memory persistence, event store, and queue implementation used for API acceptance tests.
- `src/runtime_adapters/postgres/`: PostgreSQL persistence, event store, queue, and migration/bootstrap adapter for separate API and worker processes.

The implementation defines contracts, schema, and concrete in-memory/PostgreSQL adapters. Additional repository or queue adapters can be added behind these records and ports without changing API/domain contracts.

## Core Runtime Tables

The initial PostgreSQL migration creates:

- `agent_conversations`
- `agent_messages`
- `agent_runs`
- `runtime_events`
- `runtime_outbox_events`
- `runtime_consumer_cursors`
- `runtime_async_tasks`
- `runtime_subagent_results`
- `runtime_tool_invocations`
- `runtime_approval_requests`
- `runtime_memory_scopes`
- `runtime_memory_items`
- `runtime_context_payloads`
- `runtime_compression_events`
- `runtime_capability_snapshots`
- `runtime_audit_log`
- `runtime_checkpoints`

Every tenant-scoped table includes `org_id`, and replay-critical paths include indexes for `(run_id, sequence_no)`, outbox status/availability, conversation history, run state, memory lookup, audit lookup, and checkpoint lookup.

## Record Contracts

Implemented persistence records include:

- `OutboxEventRecord`
- `RuntimeWorkerClaim`
- `RuntimeWorkerResult`
- `ConsumerCursorRecord`
- `AsyncTaskRecord`
- `SubagentResultRecord`
- `ToolInvocationRecord`
- `ApprovalRequestRecord`
- `MemoryScopeRecord`
- `MemoryItemRecord`
- `ContextPayloadRecord`
- `CompressionEventRecord`
- `CapabilitySnapshotRecord`
- `AuditLogRecord`
- `CheckpointRecord`

All records inherit the runtime contract policy: extra fields are rejected unless explicitly modeled, IDs and slugs are normalized, JSON payloads are redacted, and hashes are validated.

## Persistence Responsibilities

Runtime persistence must support:

- Conversation creation and scoped conversation lookup.
- Ordered message history for later turns in the same conversation.
- Idempotent run creation by `(org_id, user_id, idempotency_key)`.
- Append-only runtime event replay by `sequence_no`.
- Streaming model output as append-only `runtime_events` such as `model_delta`, followed by `final_response` and terminal lifecycle events.
- Outbox command claim, retry, completion, and dead-letter transitions.
- Approval request and decision persistence.
- Async subagent task state outside message history.
- Tool invocation audit state with redacted inputs/results.
- Memory metadata and content references.
- Large context payload references instead of inline blobs.
- Compression telemetry, capability snapshots, audit log rows, and checkpoint references.

## Redaction And Storage Rules

- Do not store provider API keys, OAuth tokens, session cookies, or raw credentials in runtime tables.
- Redact JSON payloads before storing events, tool invocation args/results, memory namespace metadata, and audit metadata.
- Store large connector outputs and context payloads by reference in `runtime_context_payloads`.
- Keep emitted `runtime_events` append-only.
- Keep connector-owned data in connector systems; runtime tables store references, summaries, and audit state.

## Migration Rules

- Migrations are deterministic and ordered through `PostgresMigrationCatalog`.
- The initial migration ID is `0001_agent_runtime_persistence`.
- CI should validate table coverage, tenant scoping, replay indexes, outbox indexes, and lock columns.
- Future migrations that change API/event shape must account for `schema_version` or `event_protocol_version`.

## Test Coverage

Unit tests validate persistence record normalization/redaction, hash validation, extra-field rejection, initial PostgreSQL table coverage, tenant `org_id` coverage, replay indexes, outbox indexes, and the absence of a generic `agent_state` table.
