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

## Retention Sweeper (C8)

The retention sweeper runs in the runtime worker process and applies policy
TTLs from the `retention_policies` table. Most-specific policy wins:
`conversation > assistant > user > org > deployment-default`.

- **Off by default.** Set `RETENTION_SWEEP_ENABLED=true` per deployment to
  opt in; existing deploys won't start tombstoning rows on upgrade.
- **Cadence:** every `RETENTION_SWEEP_INTERVAL_SECONDS` (default 600).
- **Dry-run:** `RETENTION_SWEEP_DRY_RUN=true` runs the sweep inside a
  rolled-back transaction so the rowcount reflects what would change
  without leaving state behind.

### Per-kind strategy

| Kind               | Strategy                                                                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `messages`         | Tombstone (`status='deleted'`, content blanked) older than TTL; conversation must not be on legal hold.                                     |
| `events`           | Redact `payload_json_redacted` + stamp `metadata_json_redacted = {"retention_purged": true}` older than TTL; run must not be on legal hold. |
| `context_payloads` | Hard delete where `retention_until < now()` (column-driven, not TTL-driven).                                                                |
| `checkpoints`      | Hard delete versions outside the latest 10 per `(thread_id, namespace)` AND outside the policy window.                                      |
| `memory_items`     | Tombstone (`deleted_at = now()`, `content_summary = '[deleted by retention policy]'`) older than TTL.                                       |

### Deployment defaults

| Profile                                               | Messages | Events   | Other kinds                     |
| ----------------------------------------------------- | -------- | -------- | ------------------------------- |
| `saas_multi_tenant`                                   | 365 days | 365 days | None (operator must set policy) |
| `single_tenant_managed` / `single_tenant_self_hosted` | None     | None     | None                            |

Single-tenant deploys are no-op until the customer seeds policies; this is
deliberate so customer data isn't deleted under our defaults.

### Operator workflow

Until the admin endpoints ship alongside A10 RBAC, policies are seeded via
SQL:

```sql
INSERT INTO retention_policies (id, org_id, scope, resource_id, kind, ttl_seconds, created_by_user_id, created_at, updated_at)
VALUES ('rp_demo', 'org_a', 'org', NULL, 'messages', 7776000, 'admin', NOW(), NOW());
```

### Audit invariants

- The sweeper never deletes rows from `runtime_audit_log` or
  `runtime_deletion_evidence`.
- Tombstone preserves the row id so audit references remain intact.
- Legal-hold-active resources are skipped; no `runtime_deletion_evidence`
  row is written for held items.

## Test Requirements

Retention changes require tests for tenant isolation, user deletion cascades,
legal hold blocking, audit evidence preservation, redaction, and expired payload
purging.
