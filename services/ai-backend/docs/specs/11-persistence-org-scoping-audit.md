# Spec: Persistence org scoping audit (runtime API + core backend)

**Status:** Audit completed for `services/ai-backend` runtime adapters and `services/backend` MCP/skills stores as of this document.  
**Related:** [10-agent-runtime-persistence-spec.md](10-agent-runtime-persistence-spec.md), multi-tenant deployment plan `02-org-scoping-store-and-sql-audit.md`.

## Policy

- Every **caller-facing** persistence API that returns tenant-owned rows **must** accept `org_id` (and `user_id` where the product scope is per-user within an org) and constrain queries accordingly.
- **Worker-internal** helpers may address rows by `run_id` alone where `run_id` is a globally unique identifier and the worker has already validated the command payload; risk is documented below.

## AI backend: `PersistencePort` (`agent_runtime/api/ports.py`)

| Method                         | Org / user in API                      | Adapter notes (`runtime_adapters/in_memory`, `postgres/runtime_api_store`)                                                                                                                                                                  |
| ------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create_conversation`          | Writes `org_id`/`user_id` from request | Inserts include org/user; idempotent lookups use `(org_id, user_id, idempotency_key)`.                                                                                                                                                      |
| `get_conversation`             | `(org_id, user_id, conversation_id)`   | `WHERE org_id AND user_id AND id`.                                                                                                                                                                                                          |
| `list_conversations`           | `(org_id, user_id)`                    | Filtered by org and user.                                                                                                                                                                                                                   |
| `list_messages`                | `(org_id, conversation_id)`            | `WHERE org_id AND conversation_id`; conversation membership enforced upstream via `_conversation_for_scope`.                                                                                                                                |
| `append_message`               | Record carries `org_id`                | Writes scoped row.                                                                                                                                                                                                                          |
| `create_run_with_user_message` | From conversation / request context    | Transaction ties run to conversation already scoped.                                                                                                                                                                                        |
| `get_run`                      | `(org_id, run_id)`                     | Postgres: `WHERE id AND org_id`. In-memory: same.                                                                                                                                                                                           |
| `update_run_status`            | **Internal:** `(run_id)` only          | Updates `WHERE id = run_id`. Assumes **unique run IDs** across tenants; intended for worker after claim. Cross-tenant risk if run_id leaked: mitigated by worker validation (see deployment plan `04-runtime-worker-tenant-validation.md`). |
| `set_run_latest_sequence`      | **Internal:** `(run_id)`               | Same pattern as status updates.                                                                                                                                                                                                             |
| `record_approval_decision`     | Record includes org                    | Scoped updates.                                                                                                                                                                                                                             |
| `create_approval_request`      | Record includes org                    | Scoped insert.                                                                                                                                                                                                                              |
| `get_approval_request`         | `(org_id, approval_id)`                | Must filter by org.                                                                                                                                                                                                                         |
| `write_audit_log`              | Record content                         | Audit rows include `org_id` in schema.                                                                                                                                                                                                      |
| `delete_user_history`          | `(org_id, user_id)`                    | Deletes/tombstones scoped.                                                                                                                                                                                                                  |

## AI backend: `EventStorePort`

| Method                | Scoping                                                                          |
| --------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `append_event`        | Serialized via `agent_runs` row lock; load uses run identity from durable graph. |
| `list_events_after`   | `(org_id, run_id)` — replay constrained.                                         |
| `get_latest_sequence` | `(run_id)` only                                                                  | Sequence is per-run; **run_id** must be globally unique. Acceptable for worker/replay after API-scoped run creation. |

## AI backend: `RuntimeQueuePort`

| Method                  | Scoping                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------ |
| `enqueue_*`             | Commands carry `org_id` / `user_id` in payload types (`RuntimeRunCommand`, etc.).    |
| `claim_next` / `mark_*` | Operates on outbox **IDs** from prior claims; org is on the outbox row from enqueue. |

## Core backend: MCP + skills (`backend_app/store.py`)

| Area        | Scoping                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------- |
| MCP servers | `org_id` + `user_id` on list/get/delete/update; SQL `WHERE org_id` (and user as applicable). |
| Skills      | Same pattern for skill records and audit append helpers.                                     |

## Findings (no code change required in this audit)

- No production **API** path should query tenant tables by `run_id` or `conversation_id` **without** `org_id` except where noted as worker-internal / global-id assumptions.
- **Defense in depth** options for shared-database multi-tenant deployments: Postgres Row-Level Security (deployment plan `03-postgres-row-level-security.md`).

## Review cadence

When adding a new `PersistencePort` or store method, update this table or add a focused unit test proving org predicates for the new path.
