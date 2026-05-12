# Request Lifecycle

How a user message travels from browser to SSE stream, end to end.

See also:

- [00-system-map.md](00-system-map.md) — module responsibilities
- [features/streaming-sse.md](../features/streaming-sse.md) — SSE resume, heartbeat, cancellation detail
- [diagrams/flows/f1-single-turn.puml](diagrams/flows/f1-single-turn.puml) — sequence diagram

---

## Request path overview

```
Browser
  → Vite proxy (dev) / nginx ingress (prod)
  → backend-facade :8200  (auth header added; routes /v1/* only)
  → ai-backend :8000      (RuntimeApiRoutes)
  → agent_runtime/api/    (domain services; creates DB rows + queue entry)
  → Worker process        (claims queue entry, drives LangGraph)
  → SSE stream            (browser reconnects with after_sequence=N)
```

The facade **does not** expose `/internal/v1/*`. Backend's `/internal/v1/*` is consumed
only by `ai-backend` workers (MCP cards, client sessions, RPC proxy, skill bundles).

---

## Phase 1 — Enqueue

**Entrypoint:** `runtime_api/http/routes.py` → `POST /v1/agent/runs`

1. `RuntimeApiRoutes` authenticates the JWT and resolves org/user via `runtime_api/auth.py`.
2. It delegates to `RuntimeApiService.create_run()` (`agent_runtime/api/service.py`).
3. `RuntimeApiService` calls `PersistencePort.create_run_with_user_message()` — one atomic
   write that creates a `ConversationRecord`, `MessageRecord` (user), and `RunRecord`
   (status=`QUEUED`).
4. It calls `RuntimeQueuePort.enqueue_run()` to push a `RuntimeRunCommand` to the durable queue.
5. Returns `202 Accepted` with `{ run_id, conversation_id }` — no streaming yet.

---

## Phase 2 — SSE connection

**Entrypoint:** `runtime_api/http/routes.py` → `GET /v1/agent/runs/{run_id}/stream`

The browser opens this immediately after receiving the `202`. The query parameter
`after_sequence=0` asks for all events from the beginning. `follow=true` (default) keeps
the connection alive until the run reaches a terminal status.

`RuntimeSseAdapter.stream()` (`runtime_api/sse/adapter.py`) runs a loop:

1. Call `service.replay_events(after_sequence=latest)` — returns events from `EventStore`.
2. Yield each event as an SSE frame (`event: runtime_event\nid: <seq>\ndata: <json>\n\n`).
3. If terminal status seen → unsubscribe from bus, return (stream closes).
4. If `follow=true` → `await event_bus.wait(run_id, timeout=2.0s)`.
5. On bus notification (or 2s timeout) → go to step 1.

---

## Phase 3 — Worker execution

**Entrypoint:** `runtime_worker/loop.py` → `RuntimeWorkerLoop.run()`

The worker polls `RuntimeQueuePort.claim_next()` in a tight loop. On each claimed item it
dispatches by `command_type`:

| `command_type`         | Handler                                           |
| ---------------------- | ------------------------------------------------- |
| `RUN_REQUESTED`        | `RuntimeRunHandler` (`handlers/run.py`)           |
| `RUN_CANCEL_REQUESTED` | `RuntimeCancelHandler` (`handlers/cancel.py`)     |
| `APPROVAL_RESOLVED`    | `RuntimeApprovalHandler` (`handlers/approval.py`) |

### `RuntimeRunHandler.handle()`

1. Calls `acreate_agent_runtime(context, deps)` (`execution/factory.py`).
   - Loads tool registry, MCP servers (HTTP call to `backend`), skill bundles, memory.
   - Compiles a LangGraph graph with the resolved capability set.
   - Returns a `RuntimeHarness` (compiled graph + deps).
2. Creates `StreamingExecutor` and calls `.run()`.
3. `StreamingExecutor` iterates the LangGraph `astream()` output via `StreamOrchestrator`.
4. Each meaningful chunk calls `RuntimeEventProducer.append_api_event()`:
   - Writes to `EventStorePort.append_event()` → assigns monotonic `sequence_no`.
   - Calls `EventBus.notify_sync(run_id)` → wakes waiting SSE adapters.
5. On completion: emits `FINAL_RESPONSE` then `RUN_COMPLETED`.
6. Calls `BudgetCharger.charge_run()` to CAS-update the budget rows.
7. Calls `queue.mark_complete(claim)`.

---

## Phase 4 — Event persistence model

Every event is stored as a `RuntimeEventEnvelope` row with:

- `run_id` — which run it belongs to
- `sequence_no` — monotonically increasing integer per run (1, 2, 3, …)
- `event_type` — one of ~45 `RuntimeApiEventType` values
- `payload` — JSON object (shape depends on `event_type`)
- `visibility` — `USER` (default), `INTERNAL`, `AUDIT`

Clients always reconnect with `after_sequence=N` — they never miss events because
**events are never deleted during a run** and the sequence is append-only.

The `EventBus` is either in-memory (single-process dev) or Postgres LISTEN/NOTIFY
(`runtime_api/sse/postgres_event_bus.py`). The bus delivers a push notification;
the SSE adapter always re-reads from the authoritative `EventStore` — the bus
is never the data source, only a wake signal.

---

## Worker topology options

Controlled by `RUNTIME_START_IN_PROCESS_WORKER` and `RUNTIME_STORE_BACKEND`:

| Mode                    | When                                   | Notes                                                              |
| ----------------------- | -------------------------------------- | ------------------------------------------------------------------ |
| In-process worker       | `RUNTIME_START_IN_PROCESS_WORKER=true` | API process spawns a worker coroutine. Used for local dev.         |
| Separate worker process | default in production                  | `runtime_worker/__main__.py` runs separately with shared Postgres. |
| In-memory adapters      | `RUNTIME_STORE_BACKEND=in_memory`      | Single-process; state lost on restart. Used in tests.              |
| Postgres adapters       | `RUNTIME_STORE_BACKEND=postgres`       | Shared store; supports multiple worker replicas.                   |

See [architecture/03-adapters.md](03-adapters.md) for adapter selection details.

---

## Approval / resume path

When a worker run hits a LangGraph interrupt (MCP auth required, tool approval, draft
send confirmation), the `StreamingExecutor` detects `action_interrupted=True` and:

1. Updates run status to `AWAITING_APPROVAL`.
2. Does **not** call `queue.mark_complete()`.

The user approves via `POST /v1/approvals/{approval_id}/decision`. This:

1. Updates the approval row.
2. Enqueues a `RuntimeApprovalResolvedCommand`.
3. Returns `202`.

The worker then claims the approval command, `RuntimeApprovalHandler` loads the
checkpoint, and `StreamingExecutor` resumes from where the graph paused.

See [features/approvals.md](../features/approvals.md) and flow diagram
[f8-mcp-auth](diagrams/flows/f8-mcp-auth.puml).

---

## Cancellation path

`POST /v1/agent/runs/{run_id}/cancel` enqueues a `RuntimeCancelCommand`. The worker
claims it, `RuntimeCancelHandler` marks the run `CANCELLED`, emits `RUN_CANCELLED`,
and calls `mark_complete`. The SSE adapter sees the terminal status and closes.

See [features/streaming-sse.md](../features/streaming-sse.md) and flow diagram
[f4-cancel](diagrams/flows/f4-cancel.puml).
