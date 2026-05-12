# Refactor PRD — Split `RuntimeApiService` (Phase 6 / P22)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §2.7](../architecture/refactor-audit.md#27-runtimeapiservice-at-24k-loc), [roadmap P22](00-roadmap.md#phase-6--coordinator-split-do-last)

---

## 0. TL;DR

`RuntimeApiService` is a 2,464-LOC class that the HTTP API depends on. (Method inventory at PR 1 showed the worker does **not** import this class — it uses ports + `RuntimeEventProducer` directly. PR 3 of the migration is effectively a no-op.) It owns command paths (create_run / cancel_run / approval decisions / conversation CRUD), query paths (list_conversations / replay_events / get_context), workspace admin, and a long tail of read helpers. Every dependent has already shrunk or been validated through Phases 1–5; the coordinator is now the only god-class left.

This PRD splits it into **five focused coordinators that share zero state** plus a deletion of the wrapper class. Each coordinator is a thin composition over the persistence / event-store / queue ports — they do not subclass, share, or proxy each other. The 8 existing domain services (Draft / Share / Fork variants / Workspace variants / Usage / MCP discovery) stay exactly as they are; this PRD does not touch them.

The migration is **composition-first, big-bang-last**: callers move one method at a time behind a thin re-export shim, with `RuntimeApiService` reduced to a 3-line delegator before the file is deleted.

**PR 1 shipped 2026-05-11.** Five coordinator shim files added under `agent_runtime/api/`, each forwarding to the legacy service. All coordinators constructed in `runtime_api/app.py` lifespan and stored on `app.state.*_coordinator` / `app.state.conversation_query_service` for PR 2 to migrate routes to. `test_import_boundaries` tightened to use word-boundary regex (the substring check produced a false positive against the legitimate new `agent_runtime.api.approval_coordinator` module). 1667 unit tests pass.

---

## 1. Problem

[`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py) is **2,464 LOC** of class body. The class owns four logically distinct domains, plus glue that connects them:

1. **Run lifecycle** — `create_run`, `cancel_run`, run status transitions, queue interaction for `RUN_REQUESTED` and `RUN_CANCEL_REQUESTED`, retry semantics, dead-letter handling on the worker side. Bridges idempotency keys to the run record.
2. **Approval lifecycle** — `record_approval_decision`, approval-row creation, `APPROVAL_RESOLVED` command emission, MCP auth resolution paired with the run that owns it. Multi-fire across token rotation per [f8](../architecture/f8-mcp-auth.puml).
3. **Read / projection paths** — `list_conversations`, `list_messages`, `get_run`, `replay_events`, `get_conversation_context` (the `headroom_pct` surface from [f9](../architecture/f9-usage-metrics.puml)). All read-only; all called by HTTP routes and the SSE adapter.
4. **Notification fan-out** — `on_event_appended` plumbing back to the bus, inbox notification orchestration, `NotificationDispatcher` invocation.

### Symptoms

- **One class is read by every dependent.** Both the FastAPI routes ([`runtime_api/http/routes.py`](../../src/runtime_api/http/routes.py)) and the worker handlers ([`runtime_worker/handlers/run.py`](../../src/runtime_worker/handlers/run.py), `cancel.py`, `approval.py`) import `RuntimeApiService`. Any change to its public surface ripples to both. A 2,464-LOC class with two distinct consumers and four orthogonal responsibilities is the textbook coordinator anti-pattern.
- **No single source of truth for state ownership.** Run state transitions are mediated through methods on the same class that also does conversation projection. Reading "who owns run cancellation?" requires reading methods that share a `self`. There is no method-level boundary you can point at and say "this is the run coordinator."
- **Substitution is all-or-nothing.** Tests that want to fake out the approval path get a fake of the entire coordinator, which then has to satisfy every other method some other code path might touch via `getattr`-style flexibility. The 38+ tests that construct a `RuntimeApiService` in setup pay for this.
- **Span naming is meaningless.** Every traced operation surfaces as `RuntimeApiService.<method>` regardless of whether it's a read or a write or a notification fan-out. Production traces look like a single hot service.
- **Idempotency invariants spread across methods.** The `idempotency_key` on retried commands is honored inside `create_run`, but the cancellation idempotency lives in `cancel_run` with subtly different rules. With one class, the rules can drift without a clear contract boundary.

### What this is NOT

- Not a consolidation of the 8 domain services. [`refactor-audit §2.6`](../architecture/refactor-audit.md#26-service-splits-inside-c4-that-should-be-one-service-each) proposed this and [`08-service-consolidation.md`](08-service-consolidation.md) retracted it with code-level evidence. Each of those services has distinct purposes. They stay.
- Not a port change. The async [`PersistencePort`](../../src/agent_runtime/api/ports.py), [`EventStorePort`](../../src/agent_runtime/api/ports.py), [`RuntimeQueuePort`](../../src/agent_runtime/api/ports.py) surfaces stay identical. [`refactor-audit §2.1`](../architecture/refactor-audit.md#21-9-persistence-ports--17-record-types) topic-split is a separate refactor noted in [`16-repository-collapse.md`](16-repository-collapse.md).
- Not a behavior change. Every existing public method keeps its signature and observable behavior. This is a layer collapse, not a semantic shift.
- Not a CQRS framework adoption. We use plain Python classes. The read / write split is conceptual, not an axon-or-EventStoreDB import.
- Not a feature flag. The change is mechanical; we don't ship two implementations in parallel. Rollback is git revert of the most recent PR in the migration sequence.

---

## 2. Design principles

This refactor is large enough that "follow the principles" is the difference between a clean split and a re-coupled mess. The principles, ranked by load:

### 2.1 One source of truth per piece of state

Each coordinator owns a slice of state. No two coordinators read or write the same row through the same code path. Conflicts are resolved through ports, not through cross-coordinator method calls.

- Run records → `RunCoordinator` only.
- Approval rows → `ApprovalCoordinator` only.
- Conversation / message / event projections → `ConversationQueryService` only (read-only).
- Bus subscriptions / inbox notifications → `NotificationCoordinator` only.

If two coordinators need the same fact (e.g. "is this run terminal?"), the read goes through `ConversationQueryService` or — when on the write path — through the persistence port directly. Coordinators do not call each other to ask questions about state.

### 2.2 Composition over wrapping

The four new coordinators are constructed independently and take ports in their constructor. They do not subclass each other, do not share a base class, do not pass `self` between each other. The current `RuntimeApiService` ends up a deletable shell.

This is deliberate. Any abstract `BaseCoordinator` becomes the next 2.4k-LOC class in two years.

### 2.3 Substitutability (LSP-style, not duck-typed)

Each coordinator is constructed via DI in [`runtime_api/app.py`](../../src/runtime_api/app.py) lifespan and in the worker's [`runtime_worker/dependencies.py`](../../src/runtime_worker/dependencies.py). Tests inject test doubles for one coordinator at a time. No test ever constructs a "fake RuntimeApiService" again because that class no longer exists.

### 2.4 Don't over-split

Four coordinators is right. Twelve is wrong. The signal for "this should be its own coordinator" is one of:

- Has a distinct lifecycle (run vs approval).
- Is read-only vs write-side (queries vs commands).
- Is cross-cutting (notifications).

Operations on the same record type with the same lifecycle stay together. `create_run` and `cancel_run` both touch run state and emit run events — they belong in the same coordinator. We are not creating a `CreateRunCoordinator` and a `CancelRunCoordinator`.

### 2.5 DRY at the contract level, not the implementation level

The four coordinators will have superficially similar shapes: each takes ports in `__init__`, each emits typed `RuntimeEventEnvelope`s, each enforces `org_id`/`user_id` checks. Resist the urge to factor a `_check_org_user_id_or_raise` helper that lives on a shared base. Each coordinator inlines the check; if a real second-order pattern emerges (e.g. an auth interceptor middleware), it lives in `agent_runtime/api/` as a module-level function called from each coordinator, never as a shared base class.

The shared substrate that _does_ exist is the existing ports — `PersistencePort`, `EventStorePort`, `RuntimeQueuePort`, and the `RuntimeEventProducer`. Coordinators compose these directly.

### 2.6 No new abstraction without two concrete uses

If we feel tempted to introduce an interface (`CoordinatorBase`, `CommandHandler`, `QueryHandler`), require **two** concrete callers before extracting. The existing four are enough load on their own.

---

## 3. Target architecture

### 3.1 The five coordinators

(Revised at PR 1 after method inventory: five coordinators land, not four. `NotificationCoordinator` is dropped — the bus plumbing it would have owned is already handled by `RuntimeEventProducer`'s `on_event_appended` callback at the construction site. `ConversationCoordinator` is added for conversation lifecycle writes. `WorkspaceCoordinator` is added for workspace admin operations that were not in the original four-way mapping.)

| New class                  | File                                                                                                           | Owns                                                                                                                                                                                     | LOC budget |
| -------------------------- | -------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| `RunCoordinator`           | [`agent_runtime/api/run_coordinator.py`](../../src/agent_runtime/api/run_coordinator.py)                       | `create_run`, `cancel_run`. Run lifecycle write path. Idempotency on retried commands. Queue interaction for `RUN_REQUESTED` and `RUN_CANCEL_REQUESTED`.                                 | ~600       |
| `ApprovalCoordinator`      | [`agent_runtime/api/approval_coordinator.py`](../../src/agent_runtime/api/approval_coordinator.py)             | `list_assigned_approvals` (inbox read), `record_approval_decision`, `request_approval_undo`. Approval-row lifecycle. `APPROVAL_RESOLVED` command emission. MCP auth coupling.            | ~500       |
| `ConversationCoordinator`  | [`agent_runtime/api/conversation_coordinator.py`](../../src/agent_runtime/api/conversation_coordinator.py)     | `create_conversation`, `update_conversation`, `update_conversation_connectors`, `delete_conversation`, `restore_conversation`, `delete_user_history`. Conversation lifecycle write path. | ~600       |
| `ConversationQueryService` | [`agent_runtime/api/conversation_query_service.py`](../../src/agent_runtime/api/conversation_query_service.py) | `list_models`, `get_conversation`, `list_conversations`, `list_messages`, `get_conversation_context` (incl. `headroom_pct`), `get_run`, `replay_events`. Read-only projection.           | ~500       |
| `WorkspaceCoordinator`     | [`agent_runtime/api/workspace_coordinator.py`](../../src/agent_runtime/api/workspace_coordinator.py)           | `get_workspace_defaults`, `update_workspace_defaults`, `request_workspace_export`, `record_workspace_delete_attempt`. Workspace-level admin.                                             | ~250       |

The `RuntimeEventProducer` ([`api/events.py`](../../src/agent_runtime/api/events.py)) stays as-is and is shared by all five. It is not a coordinator — it is the canonical event append + presentation + bus notify path. The bus subscription is wired at the `RuntimeEventProducer` construction site (`on_event_appended=event_bus.notify_sync`); no notification coordinator is needed.

`ConversationContextBuilder` ([`api/usage_service.py:236`](../../src/agent_runtime/api/usage_service.py)) stays as-is and is used only by `ConversationQueryService`. We do not move it.

The 8 existing domain services (Draft / Share / Fork variants / Workspace variants / Usage / MCP discovery) are not touched. Workspace admin operations on `WorkspaceCoordinator` continue to call into the existing `WorkspaceDefaultsService` for their actual implementation; `WorkspaceCoordinator` is the API surface the legacy class previously provided.

### 3.2 Construction graph

```
                   ┌──────────────────────────────┐
                   │   ports + RuntimeEventProducer    │
                   └─────┬────────┬────────┬─────────┘
                         │        │        │
       ┌─────────────────┘        │        └──────────────┐
       │                          │                       │
       ▼                          ▼                       ▼
RunCoordinator           ApprovalCoordinator     ConversationQueryService
       │                          │                       │
       │                          │                       │
       └──────────┬───────────────┘                       │
                  │                                       │
                  ▼                                       │
      NotificationCoordinator ◄──────────────────────────┘
                  ▲
                  │
        bus + InboxBus + NotificationDispatcher
```

Each arrow is constructor injection. No coordinator calls another coordinator at runtime — the construction graph is a tree, not a cycle. `NotificationCoordinator` takes the bus + dispatcher in its constructor; it does not call back into `RunCoordinator` or `ApprovalCoordinator`. Notifications happen because callers of `RunCoordinator.create_run` separately invoke `NotificationCoordinator` if a fan-out is needed — usually they don't, because the event-bus subscription already drives notifications via `on_event_appended`.

### 3.3 Method-level mapping

Every public method on the current `RuntimeApiService` lands in exactly one new home. The mapping below is binding: implementation must match this table.

**Revised after PR 1 method inventory** (`grep -nE "^    (async )?def " src/agent_runtime/api/service.py`). Two findings from the inventory changed the design:

1. **The worker does not import `RuntimeApiService`.** Worker handlers use ports + `RuntimeEventProducer` directly (`grep "RuntimeApiService" src/runtime_worker/` returns zero results). So `update_run_status`, `mark_run_failed`, `resolve_approval_expiry` are not coordinator-relevant — they live on the persistence port and stay there. PR 3 from the migration plan is effectively a no-op.
2. **The public surface is wider than initially mapped.** Conversation CRUD (`create_conversation`, `update_conversation`, `delete_conversation`, `restore_conversation`, `update_conversation_connectors`, `delete_user_history`) and workspace admin (`get_workspace_defaults`, `update_workspace_defaults`, `request_workspace_export`, `record_workspace_delete_attempt`) and approval reads/undo (`list_assigned_approvals`, `request_approval_undo`) are all on the legacy class. They need homes too.

The shape lands as **five coordinators** (the original four plus `ConversationCoordinator` for conversation writes; `WorkspaceCoordinator` replaces the planned `NotificationCoordinator` — the bus plumbing it would have owned is already at the `RuntimeEventProducer` construction site via the `on_event_appended` callback). PRD §3.1 LOC budgets are revised in §3.1 below.

| Current method (`RuntimeApiService.*`) | New home                                                 |
| -------------------------------------- | -------------------------------------------------------- |
| `create_run`                           | `RunCoordinator.create_run`                              |
| `cancel_run`                           | `RunCoordinator.cancel_run`                              |
| `list_assigned_approvals`              | `ApprovalCoordinator.list_assigned_approvals`            |
| `record_approval_decision`             | `ApprovalCoordinator.record_approval_decision`           |
| `request_approval_undo`                | `ApprovalCoordinator.request_approval_undo`              |
| `create_conversation`                  | `ConversationCoordinator.create_conversation`            |
| `update_conversation`                  | `ConversationCoordinator.update_conversation`            |
| `update_conversation_connectors`       | `ConversationCoordinator.update_conversation_connectors` |
| `delete_conversation`                  | `ConversationCoordinator.delete_conversation`            |
| `restore_conversation`                 | `ConversationCoordinator.restore_conversation`           |
| `delete_user_history`                  | `ConversationCoordinator.delete_user_history`            |
| `list_models`                          | `ConversationQueryService.list_models`                   |
| `get_conversation`                     | `ConversationQueryService.get_conversation`              |
| `list_conversations`                   | `ConversationQueryService.list_conversations`            |
| `list_messages`                        | `ConversationQueryService.list_messages`                 |
| `get_conversation_context`             | `ConversationQueryService.get_conversation_context`      |
| `get_run`                              | `ConversationQueryService.get_run`                       |
| `replay_events`                        | `ConversationQueryService.replay_events`                 |
| `get_workspace_defaults`               | `WorkspaceCoordinator.get_workspace_defaults`            |
| `update_workspace_defaults`            | `WorkspaceCoordinator.update_workspace_defaults`         |
| `request_workspace_export`             | `WorkspaceCoordinator.request_workspace_export`          |
| `record_workspace_delete_attempt`      | `WorkspaceCoordinator.record_workspace_delete_attempt`   |

Internal helpers (`_apply_workspace_default_model`, `_seed_default_connectors_if_needed`, `_with_latest_run`, `_resolve_user_policies`, `_resolve_suggested_connectors`, `_resolve_workspace_behavior_overrides`, `_request_with_runtime_context`, `_conversation_for_scope*`, `_run_for_scope`, `_workspace_defaults`, `_validate_workspace_default_model`, `_resolve_conversation_retention_until`, `_cancel_active_run_for_conversation`, `_decide_forwarded`, `_guard_forwardable`, `_prior_run_ids_for_chain`, `_record_to_assigned`, `_encode_assigned_cursor`, `_decode_assigned_cursor`, `_decision_decided_at`, `_chain_depth`, `_wire_status_for`, `_create_run_response`, `_undo_expires_at_for`, `_apply_conversation_scope_fallback`, `_to_json`) move with their public callers in PR 4. Any helper used by methods in two different coordinators stays on a shared module under `agent_runtime/api/` (not on a base class).

If a method does not appear in this table, it is either (a) an internal helper that moves with its public method, or (b) dead code identified during the migration and removed in a separate PR.

### 3.4 Public surface — Pydantic contracts

Every coordinator method takes typed inputs and returns typed outputs. Existing contracts in [`runtime_api/schemas/`](../../src/runtime_api/schemas/) stay; new coordinator boundaries reuse them.

```python
# agent_runtime/api/run_coordinator.py

from pydantic import BaseModel

from agent_runtime.api.ports import EventStorePort, PersistencePort, RuntimeQueuePort
from agent_runtime.api.events import RuntimeEventProducer
from runtime_api.schemas.runs import CreateRunRequest, RunRecord


class RunCoordinator:
    """Owns the run lifecycle. Single source of truth for run status transitions.

    Both the HTTP API (POST /v1/agent/runs, POST .../cancel) and the worker
    (run handler, cancel handler) call into this class. No other coordinator
    reads or writes run state.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        queue: RuntimeQueuePort,
        event_producer: RuntimeEventProducer,
    ) -> None:
        self._persistence = persistence
        self._event_store = event_store
        self._queue = queue
        self._event_producer = event_producer

    async def create_run(
        self,
        *,
        org_id: str,
        user_id: str,
        request: CreateRunRequest,
    ) -> RunRecord: ...

    async def cancel_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> RunRecord: ...

    async def update_run_status(
        self,
        *,
        run_id: str,
        status: RuntimeRunStatus,
        reason: str | None = None,
    ) -> RunRecord: ...

    async def mark_run_failed(
        self,
        *,
        run_id: str,
        error: AgentRuntimeError,
    ) -> RunRecord: ...
```

```python
# agent_runtime/api/approval_coordinator.py

class ApprovalCoordinator:
    """Owns the approval lifecycle. Single source of truth for approval state.

    Handles human-in-the-loop approvals AND MCP auth approvals through the
    same record. Multi-fire safe (token rotation per f8). Resume happens via
    a separate APPROVAL_RESOLVED queue command, not inline.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        queue: RuntimeQueuePort,
        event_producer: RuntimeEventProducer,
    ) -> None: ...

    async def record_decision(
        self,
        *,
        org_id: str,
        user_id: str,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalRecord: ...

    async def start_mcp_auth(
        self,
        *,
        org_id: str,
        run_id: str,
        server_id: str,
    ) -> McpAuthSession: ...

    async def resolve_expiry(self, *, now: datetime) -> int:
        """Sweep expired approvals; returns the count tombstoned.

        Called by the approval-expiry worker job; not callable from HTTP.
        """
        ...
```

```python
# agent_runtime/api/conversation_query_service.py

class ConversationQueryService:
    """Read-only projection over conversations, messages, runs, events.

    Callable from the HTTP API (route handlers) and the SSE adapter
    (`replay_events`). Never mutates. Returns typed Pydantic responses.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        context_builder: ConversationContextBuilder,
    ) -> None: ...

    async def list_conversations(self, *, org_id: str, user_id: str, ...) -> list[ConversationSummary]: ...
    async def list_messages(self, *, org_id: str, conversation_id: str, ...) -> list[MessageRecord]: ...
    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord: ...
    async def replay_events(self, *, run_id: str, after_sequence: int) -> RuntimeEventReplayResponse: ...
    async def get_context(self, *, org_id: str, conversation_id: str) -> ConversationContextResponse: ...
```

```python
# agent_runtime/api/notification_coordinator.py

class NotificationCoordinator:
    """Fan-out coordinator for run events + inbox notifications.

    Subscribed to RuntimeEventProducer's on_event_appended callback at
    construction time. Forwards to the run-event bus and the inbox bus.
    """

    def __init__(
        self,
        *,
        run_event_bus: EventBusBackend,
        inbox_bus: InboxBusBackend,
        dispatcher: NotificationDispatcher,
    ) -> None: ...

    def on_event_appended(self, *, run_id: str, sequence_no: int) -> None: ...
    async def notify_inbox(self, *, org_id: str, user_id: str, ...) -> None: ...
```

### 3.5 Cross-coordinator interactions (explicit list)

These are the only places one coordinator's output triggers another coordinator's input. They flow through ports / commands / events — never through direct method calls.

1. **Run start emits `RUN_STARTED`** → bus notifies → `NotificationCoordinator.on_event_appended` fires. (Indirect via `RuntimeEventProducer`.)
2. **MCP auth requires approval** → `ApprovalCoordinator.start_mcp_auth` writes an approval row + emits `MCP_AUTH_REQUIRED` → `StreamingExecutor` sees the event in `action_interrupt_events` → run pauses. (Run state transition happens in the executor; coordinator does not coordinate it directly.)
3. **Approval resolved** → `ApprovalCoordinator.record_decision` updates the row + emits `APPROVAL_RESOLVED` → enqueues `RuntimeApprovalResolvedCommand` → worker dispatches to `RuntimeApprovalHandler` → handler calls `RunCoordinator.update_run_status` to mark the run resumable. (Indirect via the queue.)
4. **Cancel** → `RunCoordinator.cancel_run` enqueues `RuntimeCancelCommand` + emits advisory `RUN_CANCELLING` → worker dispatches → handler calls back into `RunCoordinator.update_run_status(status=CANCELLED)`. (Indirect via the queue.)

No other cross-coordinator paths exist. If a fifth one shows up during implementation, stop and reconsider the boundaries before adding it.

---

## 4. Goals

1. Delete `agent_runtime/api/service.py` (the `RuntimeApiService` class).
2. Land four new coordinator files matching the contracts in §3.4, each ≤ the LOC budget in §3.1.
3. Every public method on the current `RuntimeApiService` maps 1:1 to a new coordinator method per §3.3. No public surface dropped silently.
4. Every existing test on `RuntimeApiService` either (a) migrates to test the relevant coordinator directly, or (b) is updated to construct coordinators rather than the legacy class.
5. Worker handlers and HTTP routes import the new coordinators directly. No legacy `RuntimeApiService` re-export survives the migration.
6. Trace spans are renamed to `<Coordinator>.<method>` (e.g. `RunCoordinator.create_run`).
7. No new shared base class, no new abstract interface beyond what already exists in `ports.py` + `RuntimeEventProducer`.

### Non-goals

- Touching the 8 domain services (Draft / Share / Fork variants / Workspace variants / Usage / MCP discovery). See [`08-service-consolidation.md`](08-service-consolidation.md) retraction for why they stay.
- Topic-splitting `PersistencePort` further. Tracked separately in [`16-repository-collapse.md`](16-repository-collapse.md).
- Changing the queue / event store / persistence wire formats.
- Renaming any public HTTP route or any field in `RuntimeEventEnvelope`.
- Changing the streaming model, SSE contract, or LangGraph integration.
- Replacing the `RuntimeEventProducer` (already factored out).
- Introducing CQRS, event sourcing, mediator pattern, or any architecture-style change beyond plain Python class boundaries.
- Adding a feature flag. The change is mechanical; intermediate commits work without one.

### Success criteria

- [`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py) deleted.
- Four new files exist matching §3.1 paths and LOC budgets.
- All 38+ test files that construct `RuntimeApiService` migrated.
- `grep -r "RuntimeApiService" services/ai-backend/src` returns zero results outside of git history.
- Full unit-test suite passes (`make test` and per-service `pytest`).
- Representative latency benchmark shows no regression in: SSE event delivery, run-create p99, approval-resolve p99, replay p99.
- Trace inspection: no span named `RuntimeApiService.*` after the migration.
- No `# type: ignore` comments added in the new coordinator files.

---

## 5. Migration plan

Five PRs, each safe in isolation. The system runs at every commit between them.

### PR 1 — Introduce coordinators as composition

Add the four new coordinator classes. Each class delegates to the existing `RuntimeApiService` for every method body:

```python
# agent_runtime/api/run_coordinator.py (PR 1 version)

class RunCoordinator:
    def __init__(self, *, legacy: RuntimeApiService) -> None:
        self._legacy = legacy

    async def create_run(self, *, org_id: str, user_id: str, request: CreateRunRequest) -> RunRecord:
        return await self._legacy.create_run(org_id=org_id, user_id=user_id, request=request)
    ...
```

Construct them in [`runtime_api/app.py`](../../src/runtime_api/app.py) lifespan and [`runtime_worker/dependencies.py`](../../src/runtime_worker/dependencies.py). Export them from a new module so callers can import.

Zero behavior change. `RuntimeApiService` still exists and is still the implementation. The new classes are thin shims.

**Why this PR first:** establishes the new public surface without touching call sites. Lets the rest of the migration be local to one consumer at a time.

### PR 2 — Migrate HTTP routes

Update every handler in [`runtime_api/http/routes.py`](../../src/runtime_api/http/routes.py) (and sibling route modules) to depend on the new coordinators instead of `RuntimeApiService`.

For each route handler:

1. Replace the dependency declaration.
2. Replace the method call.
3. Run the route test for that handler.

No new tests in this PR — existing tests pin the HTTP contract and will fail if a wiring mistake happens.

### PR 3 — Migrate worker handlers

Update [`runtime_worker/handlers/run.py`](../../src/runtime_worker/handlers/run.py), [`cancel.py`](../../src/runtime_worker/handlers/cancel.py), [`approval.py`](../../src/runtime_worker/handlers/approval.py), and the worker background jobs to depend on the new coordinators.

Worker lifecycle wiring in [`runtime_worker/dependencies.py`](../../src/runtime_worker/dependencies.py) changes to construct the four coordinators rather than the legacy service.

### PR 4 — Implementation inversion

For each public method on `RuntimeApiService`, **move the method body** into the matching coordinator. The legacy class's method becomes a 1-line forward into the coordinator:

```python
# RuntimeApiService.create_run (PR 4 version)
async def create_run(self, **kwargs) -> RunRecord:
    return await self._run_coordinator.create_run(**kwargs)
```

This is the largest PR but mechanically simple — each method body moves once, no logic changes. Tests that already passed in PRs 2 and 3 should still pass.

After this PR, `RuntimeApiService` is a thin delegator (~150 LOC of forwards). Nothing else in the codebase has changed since PR 3, but the implementation now lives where it belongs.

### PR 5 — Delete the legacy class

- Delete `RuntimeApiService` from `agent_runtime/api/service.py`. Delete the file.
- Update DI in `runtime_api/app.py` and `runtime_worker/dependencies.py` to construct coordinators directly from ports, removing the legacy intermediate.
- Migrate the remaining test files (the ones that still set up a `RuntimeApiService` to test something coordinator-shaped).
- Rename trace spans (`tracer.start_as_current_span("RuntimeApiService.foo")` → `"<NewCoordinator>.<method>"`).

After this PR, `grep -r "RuntimeApiService" services/ai-backend/src` returns nothing.

### Why this sequence

Each PR has a single concern. PR 1 is additive (no deletion). PR 2 and PR 3 migrate callers one consumer at a time. PR 4 moves implementation without changing surface. PR 5 deletes. At every commit, the system still works.

The alternative — a single big-bang PR — is rejected because the diff is too large to review, the rollback is all-or-nothing, and a single bug blocks the entire change.

---

## 6. Behaviors preserved

Pinned tests for each invariant. Sourced from [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved).

### Run lifecycle (RunCoordinator)

- Every public method retains its current signature (typed `org_id`/`user_id`/`request` keyword args).
- `create_run` is idempotent on `(org_id, idempotency_key)` — retried command returns the same `RunRecord`.
- `cancel_run` enqueues `RuntimeCancelCommand` as a separate command type, not inline.
- `update_run_status` honors the state-machine constraints in the existing code (e.g. terminal states cannot transition).
- `mark_run_failed` records the typed `AgentRuntimeError` with its `RuntimeErrorCode`.

### Approval lifecycle (ApprovalCoordinator)

- `record_decision` is idempotent on `(approval_id, decision)`.
- `AWAITING_APPROVAL` run state is reachable and resumable.
- Approval row survives worker restart and SSE drop.
- Multi-fire on token rotation: the same approval cycle can fire again mid-run.
- Resume happens via a separate `APPROVAL_RESOLVED` queue command, never inline.
- MCP auth approvals share the approval-row schema with human approvals.

### Read paths (ConversationQueryService)

- `replay_events` returns events with `sequence_no > after_sequence` exactly once, in order.
- `get_context` returns server-computed `headroom_pct` (integer, no FE derivation).
- `list_conversations` respects org/user scoping.
- All read methods are side-effect free.

### Notification (NotificationCoordinator)

- `on_event_appended(run_id, sequence_no)` is called for every event appended via `RuntimeEventProducer`.
- SSE wire format unchanged.
- Inbox notification fan-out unchanged.

### Cross-cutting

- `RuntimeEventEnvelope` schema unchanged.
- Trace context propagation (`trace_propagation` dict on queue commands) unchanged.
- Field-level redaction at Pydantic validator time unchanged.
- DI substitutability — every coordinator can be replaced with a test double in isolation.

---

## 7. Risks

| Risk                                                               | Mitigation                                                                                                                                                                                         | Severity |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| **A method on `RuntimeApiService` we forgot is silently dropped.** | The §3.3 mapping table is binding. CI fails if any public method exists on the legacy class without a row. A pre-PR-1 grep catalogs the surface.                                                   | High     |
| **Cross-coordinator dependency creeps in.**                        | §3.5 lists the only allowed cross-coordinator paths. Code review rejects any coordinator-to-coordinator method call.                                                                               | Medium   |
| **Test fakes proliferate.**                                        | Tests construct only the coordinators they exercise. A shared `make_coordinators()` fixture in `conftest.py` exists for tests that need all four; no per-coordinator base fixture is added.        | Medium   |
| **PR 4 (implementation move) is too large to review.**             | If the diff exceeds ~3,000 LOC, split by coordinator — PR 4a moves `RunCoordinator` bodies, PR 4b moves `ApprovalCoordinator` bodies, etc.                                                         | Medium   |
| **Worker breaks during a between-PRs window.**                     | PRs 2 and 3 both leave the legacy class wired. PR 4 keeps surface intact. PR 5 is the only deletion. Staging deploy of every PR before merge to main.                                              | Medium   |
| **A behavior invariant we did not enumerate regresses.**           | Run the entire integration suite (not just unit tests) at PR 2, PR 3, PR 4, PR 5. Pre-PR-1 latency benchmark establishes baseline; benchmark re-run at PR 5.                                       | Medium   |
| **`on_event_appended` becomes a circular dependency.**             | `NotificationCoordinator` subscribes to `RuntimeEventProducer`'s callback; it does not call any other coordinator back. The bus wakeup → SSE replay path is unchanged and one-directional.         | Low      |
| **Span naming change confuses dashboards.**                        | Trace span names are listed in §4 success criteria. Coordinate with the on-call team to update Grafana / dashboard queries in the same change set.                                                 | Low      |
| **Span / log correlation across the split breaks.**                | Trace context (W3C `traceparent`) is propagated through ports and queue commands; coordinators inherit the same span context. P13 work means OTel propagation already crosses the worker boundary. | Low      |

---

## 8. Unit testing requirements

### 8.1 New tests added

For each coordinator:

- A `tests/unit/agent_runtime/api/test_<coordinator>.py` file with the public method contract pinned.
- One test per behavior listed in §6 for that coordinator.
- Substitution test: each coordinator constructed with a `MagicMock` of its ports verifies the right port methods are called.

### 8.2 Existing tests migrated

Inventory before PR 1: ~38 test files construct `RuntimeApiService`. Categorize each:

- **Coordinator-scoped tests** — already test one slice of behavior. Migrate to construct only that coordinator. (Bulk of the work.)
- **Integration tests** — exercise multiple coordinators in concert. Update setup to construct all four; the test body should still pass.
- **Tests of methods that don't survive** (any forwarders to domain services that are dropped per §3.3) — rewrite to call the domain service directly.

### 8.3 Cross-PR test discipline

- After PR 1: existing test suite passes with zero changes (new coordinators are dead-code shims).
- After PR 2: HTTP route tests pass with coordinator wiring.
- After PR 3: worker handler tests pass with coordinator wiring.
- After PR 4: full suite passes; implementation has moved but surface unchanged.
- After PR 5: full suite passes; legacy class gone.

A test that requires `RuntimeApiService` to exist after PR 5 is itself a bug — convert it.

### 8.4 Golden / pinned outputs

- A representative integration test runs an end-to-end turn (create_run → MODEL_DELTA stream → FINAL_RESPONSE → RUN_COMPLETED) and pins the resulting event log byte-for-byte. This catches accidental schema or ordering drift across all four coordinators in one assertion.
- An approval-resume test runs a turn with `MCP_AUTH_REQUIRED` → user resolves → run resumes → completes. Pins the event sequence and the final approval-row state.

---

## 9. Rollback plan

Each PR rolls back via `git revert`. Because the migration is composition-first and the legacy class survives until PR 5, the first four PRs are individually safe to revert.

PR 5 (the deletion) is the only PR where a revert is not trivial — it requires restoring `RuntimeApiService` AND restoring the call sites. Before merging PR 5:

- Confirm all dashboards / alerting query the new span names.
- Confirm representative latency benchmarks pass.
- Pause the merge for one stabilization day after PR 4 in staging.

If a regression is found post-PR 5, the revert is: `git revert HEAD~N..HEAD` for the relevant PRs and a hotfix forward — the legacy class can be restored from history, but the migration's coupling-removal value is lost.

---

## 10. Observability

### Trace spans

After PR 5, all coordinator spans use the new names:

- `RunCoordinator.create_run`
- `RunCoordinator.cancel_run`
- `RunCoordinator.update_run_status`
- `ApprovalCoordinator.record_decision`
- `ApprovalCoordinator.start_mcp_auth`
- `ConversationQueryService.list_conversations`
- `ConversationQueryService.replay_events`
- `ConversationQueryService.get_context`
- `NotificationCoordinator.on_event_appended`

### Metrics

No new metrics introduced. Existing counters / timers continue to fire with their current names (which derive from method names — they get renamed implicitly when spans rename). Coordinate with observability owners to update any hand-pinned metric name lookups.

### Logs

Structured log fields unchanged. Existing JSON log lines tag the current span name; this updates automatically.

---

## 11. Edge cases

### 11.1 Idempotency on retried commands

The current `RuntimeApiService.create_run` honors `idempotency_key`. After the split, this still lives on `RunCoordinator.create_run`. The behavior must be identical — retrying the same `(org_id, idempotency_key)` returns the existing run, not a new one.

A pinned test covers: two concurrent calls to `create_run` with the same key return the same `RunRecord` and exactly one queue command is enqueued.

### 11.2 Approval row outliving the run

If a run is cancelled while an approval is `AWAITING_APPROVAL`, the approval row must remain queryable (for audit) but cannot resolve into a resumed run. `ApprovalCoordinator.record_decision` checks the run status before queueing a resume; if the run is terminal, the decision is recorded but no `RuntimeApprovalResolvedCommand` is enqueued.

### 11.3 Event append races with replay

`ConversationQueryService.replay_events(after_sequence=N)` reads events with `sequence_no > N`. If the worker is appending event `N+1` concurrently, the read either sees it (transaction order) or does not (race). Either is correct — the SSE adapter will reconnect-and-replay if it missed a wakeup. This is preserved unchanged.

### 11.4 Multi-fire approval on token rotation

A single run may fire `MCP_AUTH_REQUIRED` multiple times if tokens expire mid-run. Each fire creates a new approval row. `ApprovalCoordinator.record_decision` operates on the specific `approval_id`; multiple decisions in one run is normal. Pinned test covers two consecutive token-rotation cycles in one run.

### 11.5 Worker restart between command enqueue and command claim

`RunCoordinator.cancel_run` enqueues `RuntimeCancelCommand` and returns. If the worker restarts before claiming, the command sits in the queue and is claimed on restart. Cancellation is durable. This is preserved unchanged.

### 11.6 Coordinator constructed with mismatched ports

DI mistake: `RunCoordinator` constructed with a `PersistencePort` from one settings env and an `EventStorePort` from another. The legacy class was vulnerable to this; the new code is not better-armored. We deliberately do not add runtime checks — the lifespan / dependencies modules are the only construction sites and review catches this.

---

## 12. Security considerations

### 12.1 Authorization checks

Every coordinator method that takes `org_id` and `user_id` enforces them. The pattern is identical across coordinators:

```python
record = await self._persistence.get_run(run_id)
if record.org_id != org_id or record.user_id != user_id:
    raise AgentRuntimeError(code=RuntimeErrorCode.NOT_FOUND, ...)
```

We do not extract this into a shared decorator or interceptor. Inline checks are explicit, auditable, and resist subtle middleware bugs. If the same check ends up in 12 places and someone proposes a middleware in a future PR, that's the right time — not now.

### 12.2 Treat caller identity as untrusted

Per the project [`CLAUDE.md`](../../CLAUDE.md) rule: caller-supplied `org_id` / `user_id` is untrusted unless derived from a verified session/token. The HTTP routes feed verified identity from `RuntimeServiceAuthenticator`; the worker feeds identity from the claimed command's persisted fields. Coordinators trust their callers — the boundary is at the route / worker entry point.

### 12.3 Redaction

Pydantic field validators on `RuntimeEventEnvelope.payload` and `.metadata` continue to invoke the structural redactor from P11. Coordinators do not bypass this — every event flows through `RuntimeEventProducer.append_api_event`.

---

## 13. Implementation notes for the engineer doing this

A few directional notes for whoever picks this up:

1. **Start with PR 1.** Don't try to be clever with branching strategies. PR 1 ships the new shape; everything else fills it in.
2. **Pre-PR-1 deliverable: the method inventory.** Before opening PR 1, paste `grep -E "^\s+async def |^\s+def " services/ai-backend/src/agent_runtime/api/service.py` into the PR description. Annotate each method with its target coordinator per §3.3. If a method is missing from the mapping, update §3.3 in this PRD in the same PR.
3. **Resist adding a `BaseCoordinator`.** The next person to think this is a good idea will read the rejection in §2.2 and §2.6 of this PRD.
4. **Do not move helper functions into a shared module.** If `RunCoordinator` and `ApprovalCoordinator` both have a `_validate_org_user` helper, leave both. If the duplication becomes 4 callsites, then extract — not before.
5. **Read [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved) in full.** Every invariant there is non-negotiable.
6. **Read [`08-service-consolidation.md`](08-service-consolidation.md) retraction.** The lesson — distinct services often have distinct purposes that don't show in a diagram — applies to the four coordinator boundaries too. If during implementation it becomes clear that two of the four are actually one, stop and discuss before merging the split.

---

_Last updated: May 2026. This PRD reflects code state after Phases 1–5. Refresh §3.3 method inventory immediately before PR 1 so the mapping reflects current public surface._
