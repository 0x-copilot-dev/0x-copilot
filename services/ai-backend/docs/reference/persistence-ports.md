# Persistence Ports Reference

All port protocols that adapters must implement. Defined in two files:

- `agent_runtime/api/ports.py` — the three primary ports (persistence, event store, queue)
- `agent_runtime/persistence/ports.py` — higher-level capability-specific ports

All methods are `async def`. No sync variants exist. See [architecture/03-adapters.md](../architecture/03-adapters.md)
for adapter implementations.

---

## `PersistencePort`

`agent_runtime/api/ports.py`

Core CRUD for conversations, messages, runs, approvals, usage, budgets, and retention.

### Conversations and messages

| Method                | Signature                                     | Returns                      |
| --------------------- | --------------------------------------------- | ---------------------------- |
| `create_conversation` | `(org_id, user_id, title, …)`                 | `ConversationRecord`         |
| `get_conversation`    | `(org_id, conversation_id)`                   | `ConversationRecord \| None` |
| `list_conversations`  | `(org_id, user_id, limit, cursor)`            | `list[ConversationRecord]`   |
| `update_conversation` | `(conversation_id, **updates)`                | `ConversationRecord`         |
| `delete_conversation` | `(org_id, conversation_id)`                   | `None`                       |
| `list_messages`       | `(org_id, conversation_id, after_id, limit)`  | `list[MessageRecord]`        |
| `append_message`      | `(org_id, conversation_id, role, content, …)` | `MessageRecord`              |
| `get_message`         | `(org_id, message_id)`                        | `MessageRecord \| None`      |

### Runs

| Method                         | Signature                                                      | Returns                           |
| ------------------------------ | -------------------------------------------------------------- | --------------------------------- |
| `create_run_with_user_message` | `(org_id, user_id, conversation_id, content, model_config, …)` | `tuple[RunRecord, MessageRecord]` |
| `get_run`                      | `(org_id, run_id)`                                             | `RunRecord \| None`               |
| `list_runs`                    | `(org_id, conversation_id, limit)`                             | `list[RunRecord]`                 |
| `update_run_status`            | `(run_id, status, version)`                                    | `RunRecord` (CAS)                 |

### Approvals

| Method                    | Signature                                     | Returns                         |
| ------------------------- | --------------------------------------------- | ------------------------------- |
| `create_approval_request` | `(org_id, run_id, kind, payload, expires_at)` | `ApprovalRequestRecord`         |
| `get_approval_request`    | `(org_id, approval_id)`                       | `ApprovalRequestRecord \| None` |
| `update_approval_status`  | `(approval_id, status, decision)`             | `ApprovalRequestRecord`         |
| `list_approval_requests`  | `(org_id, run_id)`                            | `list[ApprovalRequestRecord]`   |
| `sweep_expired_approvals` | `(cutoff_at)`                                 | `int` (rows updated)            |

### Usage

| Method             | Signature                | Returns                             |
| ------------------ | ------------------------ | ----------------------------------- |
| `record_run_usage` | `(run_id, usage_record)` | `None`                              |
| `list_run_usage`   | `(org_id, run_id)`       | `list[RuntimeModelCallUsageRecord]` |

### Budgets

| Method                   | Signature                                     | Returns                   |
| ------------------------ | --------------------------------------------- | ------------------------- |
| `lookup_budgets_for_run` | `(org_id, user_id)`                           | `list[BudgetRecord]`      |
| `charge_budget`          | `(budget_id, amount_micro_usd, version)`      | `BudgetRecord` (CAS)      |
| `reserve_budget`         | `(org_id, user_id, run_id, amount_micro_usd)` | `BudgetReservationRecord` |
| `release_reservation`    | `(reservation_id)`                            | `None`                    |

### Retention

| Method                    | Signature                                    | Returns                       |
| ------------------------- | -------------------------------------------- | ----------------------------- |
| `list_retention_policies` | `(org_id)`                                   | `list[RetentionPolicyRecord]` |
| `sweep_retention_kind`    | `(kind: RetentionKind, cutoff_at: datetime)` | `int` (rows deleted)          |

---

## `EventStorePort`

`agent_runtime/api/ports.py`

Append-only event log. Sequence numbers are assigned by the store — callers never
set `sequence_no`.

| Method                    | Signature                                     | Returns                                         |
| ------------------------- | --------------------------------------------- | ----------------------------------------------- |
| `append_event`            | `(envelope: RuntimeEventEnvelope)`            | `RuntimeEventEnvelope` (with `sequence_no` set) |
| `append_events_batch`     | `(envelopes: list[RuntimeEventEnvelope])`     | `list[RuntimeEventEnvelope]`                    |
| `list_events_after`       | `(run_id, after_sequence, visibility_filter)` | `list[RuntimeEventEnvelope]`                    |
| `get_latest_sequence`     | `(run_id)`                                    | `int`                                           |
| `set_run_latest_sequence` | `(run_id, sequence_no)`                       | `None`                                          |

**Important:** `list_events_after(after_sequence=N)` returns envelopes with
`sequence_no > N`. A client that received up to `sequence_no=42` reconnects with
`after_sequence=42` to get `43, 44, …`.

---

## `RuntimeQueuePort`

`agent_runtime/api/ports.py`

Durable command queue. Workers claim one item at a time under an advisory lock.

| Method                      | Signature                                         | Returns                      |
| --------------------------- | ------------------------------------------------- | ---------------------------- |
| `enqueue_run`               | `(command: RuntimeRunCommand)`                    | `None`                       |
| `enqueue_cancel`            | `(command: RuntimeCancelCommand)`                 | `None`                       |
| `enqueue_approval_resolved` | `(command: RuntimeApprovalResolvedCommand)`       | `None`                       |
| `claim_next`                | `(worker_id, lock_expires_at)`                    | `RuntimeWorkerClaim \| None` |
| `mark_complete`             | `(claim: RuntimeWorkerClaim)`                     | `None`                       |
| `mark_retry`                | `(claim: RuntimeWorkerClaim, retry_at: datetime)` | `None`                       |
| `mark_dead_letter`          | `(claim: RuntimeWorkerClaim)`                     | `None`                       |

`claim_next` uses `SELECT … FOR UPDATE SKIP LOCKED` in Postgres to give each worker
exactly one item. If the worker crashes holding a lock, the lock expires at
`lock_expires_at` and the item becomes claimable again.

---

## Higher-level ports (`agent_runtime/persistence/ports.py`)

### `DraftStorePort`

| Method                                                                      | Returns               |
| --------------------------------------------------------------------------- | --------------------- |
| `upsert_draft(run_id, conversation_id, org_id, user_id, content, metadata)` | `DraftRecord`         |
| `get_draft(org_id, draft_id)`                                               | `DraftRecord \| None` |
| `list_drafts(org_id, conversation_id)`                                      | `list[DraftRecord]`   |
| `update_draft(draft_id, content, metadata)`                                 | `DraftRecord`         |
| `delete_draft(draft_id)`                                                    | `None`                |
| `mark_sent(draft_id)`                                                       | `DraftRecord`         |

### `CitationStorePort`

| Method                                                             | Returns                |
| ------------------------------------------------------------------ | ---------------------- |
| `insert_or_get(run_id, connector, doc_id, title, url, snippet, …)` | `CitationRecord`       |
| `list_for_run(run_id)`                                             | `list[CitationRecord]` |

Idempotent on `(run_id, connector, doc_id)`.

### `ConversationToolOrdinalStorePort`

| Method                                                                   | Returns                  |
| ------------------------------------------------------------------------ | ------------------------ |
| `record(conversation_id, tool_call_id, conversation_ordinal, tool_name)` | `OrdinalBinding`         |
| `get_by_tool_call_id(tool_call_id)`                                      | `OrdinalBinding \| None` |
| `get_next_ordinal(conversation_id)`                                      | `int`                    |

Idempotent on `tool_call_id`.

### `SourceStorePort`

| Method                                                          | Returns                 |
| --------------------------------------------------------------- | ----------------------- |
| `upsert_source(conversation_id, connector, doc_id, title, url)` | `SourceRecord`          |
| `aggregate_for_conversation(conversation_id)`                   | `list[SourceAggregate]` |

### `SubagentStorePort`

| Method                                                                      | Returns                   |
| --------------------------------------------------------------------------- | ------------------------- |
| `create_subagent_run(subagent_id, task_id, parent_run_id, conversation_id)` | `SubagentRunRecord`       |
| `update_subagent_status(subagent_id, status, result_summary)`               | `SubagentRunRecord`       |
| `list_for_run(parent_run_id)`                                               | `list[SubagentRunRecord]` |

### `ShareStorePort`

| Method                                                     | Returns               |
| ---------------------------------------------------------- | --------------------- |
| `create_share(conversation_id, org_id, user_id, snapshot)` | `ShareRecord`         |
| `get_share(share_id)`                                      | `ShareRecord \| None` |
| `delete_share(share_id)`                                   | `None`                |

### `CheckpointStorePort`

| Method                                     | Returns         |
| ------------------------------------------ | --------------- |
| `save_checkpoint(run_id, checkpoint_blob)` | `None`          |
| `load_checkpoint(run_id)`                  | `bytes \| None` |
| `delete_checkpoint(run_id)`                | `None`          |

Used by `RuntimeApprovalHandler` to restore LangGraph graph state on resume.

---

## `RuntimeStoreLifecyclePort`

`agent_runtime/api/ports.py`

Optional setup/teardown hooks for adapters that need connection pool management.

| Method       | When called             |
| ------------ | ----------------------- |
| `setup()`    | At application startup  |
| `teardown()` | At application shutdown |
