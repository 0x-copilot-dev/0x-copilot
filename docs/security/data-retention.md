# Data Retention, Deletion, And Legal Hold

## Data Classes

- Conversations and messages: user-visible history stored in
  `agent_conversations` and `agent_messages`.
- Runtime runs and events: operational trace data stored in `agent_runs`,
  `runtime_events`, outbox rows, approvals, tool invocations, checkpoints, and
  context payload references.
- Audit records: security evidence stored in `runtime_audit_log` and deletion
  proofs in `runtime_deletion_evidence`.
- Connector credentials and metadata: MCP server records, auth sessions,
  connections, and encrypted token envelopes.

## User Deletion

`DELETE /v1/agent/history` soft-deletes user-visible history for the authenticated
user. The runtime archives conversations, tombstones message content, cancels
active runs, preserves audit/event evidence, and writes a deletion evidence row.

Audit records are retained with minimal metadata so compliance evidence survives
without retaining deleted message content.

## Legal Hold

`runtime_legal_holds` blocks deletion for active org, user, or conversation
holds. Holds must include reason, creator, created time, and release metadata.
When a hold is active, deletion returns a conflict instead of altering history.

## Purge And Export

Expired payload references should be purged by scheduled jobs using
`retention_until` on `runtime_context_payloads`. Customer export tooling must
include conversations, messages, runs, events, approvals, tool invocations,
memory, checkpoint references, deletion evidence, and audit records.

## Test Requirements

Retention changes require tests for tenant isolation, user deletion cascades,
legal hold blocking, audit evidence preservation, redaction, and expired payload
purging.
