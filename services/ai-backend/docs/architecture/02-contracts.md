# Contracts

Every IO boundary in this service is protected by a Pydantic contract. This doc catalogs
the key contracts at each layer. For port method signatures, see
[reference/persistence-ports.md](../reference/persistence-ports.md). For the full event
type enum and payload shapes, see [reference/event-types.md](../reference/event-types.md).

---

## The envelope — `RuntimeEventEnvelope`

`runtime_api/schemas/events.py`

The single canonical unit flowing from worker → event store → SSE → browser.
Every persisted event is one envelope row.

| Field                    | Type                          | Notes                                                                   |
| ------------------------ | ----------------------------- | ----------------------------------------------------------------------- |
| `event_protocol_version` | `str`                         | Always `"1"` currently                                                  |
| `event_id`               | `str`                         | UUID4 assigned at creation                                              |
| `sequence_no`            | `PositiveInt`                 | Monotonic per run; clients resume at `after_sequence=N`                 |
| `created_at`             | `datetime`                    | UTC timestamp                                                           |
| `run_id`                 | `str`                         | Which run this event belongs to                                         |
| `conversation_id`        | `str`                         | Parent conversation                                                     |
| `trace_id`               | `str`                         | OTEL trace id for the run                                               |
| `source`                 | `StreamEventSource`           | `WORKER`, `TOOL`, `SUBAGENT`, `SYSTEM`                                  |
| `event_type`             | `RuntimeApiEventType`         | ~45 values; see [reference/event-types.md](../reference/event-types.md) |
| `span_id`                | `str \| None`                 | OTEL span                                                               |
| `parent_event_id`        | `str \| None`                 | For hierarchical subagent events                                        |
| `task_id`                | `str \| None`                 | Subagent task id                                                        |
| `subagent_id`            | `str \| None`                 | Which subagent emitted this                                             |
| `display_title`          | `str \| None`                 | Pre-projected UI label                                                  |
| `summary`                | `str \| None`                 | Pre-projected UI summary                                                |
| `status`                 | `str \| None`                 | Pre-projected UI status                                                 |
| `activity_kind`          | `RuntimeActivityKind \| None` | Pre-projected activity slot                                             |
| `visibility`             | `RuntimeEventVisibility`      | `USER` (default), `INTERNAL`, `AUDIT`                                   |
| `redaction_state`        | `RuntimeEventRedactionState`  | `REDACTED` (default)                                                    |
| `presentation`           | `dict`                        | Pre-projected presentation fields (title, subtitle, badge, …)           |
| `payload`                | `dict`                        | Event-type–specific JSON payload                                        |
| `metadata`               | `dict`                        | Operational metadata (e.g. `transient: true` for heartbeats)            |

### Projection invariant

The `presentation`, `display_title`, `summary`, `status`, and `activity_kind` fields are
**always projected by `RuntimeEventPresentationProjector`** at write time in
`RuntimeEventProducer`. The frontend must never re-derive these from event-name
prefixes or payload inspection.

---

## Domain execution contracts

`agent_runtime/execution/contracts.py`

| Contract              | Purpose                                                                                                                                |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `AgentRuntimeContext` | Immutable per-run context: `run_id`, `conversation_id`, `org_id`, `user_id`, `trace_id`, `model_config`, `runtime_settings`            |
| `RuntimeDependencies` | Mutable per-run deps: tool registry, MCP registry, skill manifest, memory backend, citation ledger, ordinal allocator, subagent runner |
| `StreamEvent`         | Normalised chunk from a provider adapter before it becomes an `RuntimeEventEnvelope`                                                   |
| `StreamEventSource`   | Enum: `WORKER`, `TOOL`, `SUBAGENT`, `SYSTEM`                                                                                           |
| `StreamEventType`     | Low-level chunk type enum used inside the worker                                                                                       |
| `RuntimeContract`     | Base Pydantic model for all domain contracts                                                                                           |

---

## Port protocols — overview

Full signatures live in [reference/persistence-ports.md](../reference/persistence-ports.md).

### `agent_runtime/api/ports.py`

Three protocols every adapter must implement:

**`PersistencePort`** — conversations, messages, runs, approvals, usage, budgets, retention.

**`EventStorePort`** — append-only event log.

```python
append_event(envelope: RuntimeEventEnvelope) -> RuntimeEventEnvelope
append_events_batch(envelopes: list[RuntimeEventEnvelope]) -> list[RuntimeEventEnvelope]
list_events_after(run_id, after_sequence, visibility_filter) -> list[RuntimeEventEnvelope]
get_latest_sequence(run_id) -> int
set_run_latest_sequence(run_id, sequence_no) -> None
```

**`RuntimeQueuePort`** — durable command queue.

```python
enqueue_run(command: RuntimeRunCommand) -> None
enqueue_cancel(command: RuntimeCancelCommand) -> None
enqueue_approval_resolved(command: RuntimeApprovalResolvedCommand) -> None
claim_next(worker_id, lock_expires_at) -> RuntimeWorkerClaim | None
mark_complete(claim: RuntimeWorkerClaim) -> None
mark_retry(claim: RuntimeWorkerClaim, retry_at) -> None
mark_dead_letter(claim: RuntimeWorkerClaim) -> None
```

### `agent_runtime/persistence/ports.py`

Higher-level ports for capability-specific state:

| Port                               | Owns                                                            |
| ---------------------------------- | --------------------------------------------------------------- |
| `DraftStorePort`                   | Draft CRUD (`upsert_draft`, `get_draft`, `delete_draft`)        |
| `CitationStorePort`                | Per-run citation rows (idempotent on `run_id+connector+doc_id`) |
| `ConversationToolOrdinalStorePort` | Monotonic ordinal binding per tool call, per conversation       |
| `SourceStorePort`                  | Aggregate source list per conversation (for Sources tab)        |
| `SubagentStorePort`                | Subagent run records                                            |
| `ShareStorePort`                   | Conversation share snapshots                                    |
| `CheckpointStorePort`              | LangGraph checkpoint blobs                                      |
| `PayloadStoragePort`               | Large payload refs (S3 or local)                                |
| `MemoryMetadataPort`               | Memory scope metadata                                           |

---

## HTTP schemas

`runtime_api/schemas/` contains one module per domain area. All are Pydantic models.

| Module             | Key schemas                                                                        |
| ------------------ | ---------------------------------------------------------------------------------- |
| `runs.py`          | `CreateRunRequest`, `RunRecord`, `AgentRunStatus`                                  |
| `conversations.py` | `ConversationRecord`, `MessageRecord`, `MessageRole`                               |
| `approvals.py`     | `ApprovalRequestRecord`, `ApprovalDecision`, `ApprovalParam`                       |
| `commands.py`      | `RuntimeRunCommand`, `RuntimeCancelCommand`, `RuntimeApprovalResolvedCommand`      |
| `usage.py`         | `RuntimeRunUsageRecord`, `RuntimeModelCallUsageRecord`, `UsageResponse`            |
| `budgets.py`       | `BudgetRecord`, `BudgetReservationRecord`                                          |
| `events.py`        | `RuntimeEventEnvelope`, `RuntimeApiEventType`, `RuntimeEventPresentationProjector` |
| `common.py`        | `AgentRunStatus`, `RuntimeActivityKind`, `RuntimeApiEventType`, `ApprovalCategory` |

---

## Pydantic policy at boundaries

1. **Every** function that crosses a module boundary accepts and returns Pydantic models,
   not `dict[str, Any]`.
2. `RuntimeContract` is the base for all domain contracts. Use `model_validate()` on
   untrusted inputs (model output, connector payloads, MCP descriptors).
3. Never pass `dict[str, Any]` as long-lived domain state. Convert at the boundary,
   propagate the typed model.
4. Constrained string types (`Field(min_length=1, max_length=N)`) on all identifier
   and user-supplied text fields.
5. Convert broad exceptions to typed domain errors (`AgentRuntimeError`) with a
   safe public message before they can reach model output or HTTP responses.

---

## Untrusted inputs (must validate)

- Model output (tool call args, reasoning text)
- MCP tool results and descriptors (tool schemas, resource lists)
- Memory content (written by a previous turn; may be stale or injected)
- Connector/tool payloads
- HTTP request bodies from `backend-facade`

See `agent_runtime/execution/errors.py` for typed domain errors, and
`agent_runtime/observability/redactor.py` for payload sanitisation.
