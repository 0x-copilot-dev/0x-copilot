# PRD-AR-I3 — Durable agent work items

**Goal:** Give users and product automations a durable, inspectable work breakdown whose ready items dispatch ordinary agent runs without creating a second runtime queue, lease protocol, or execution engine.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Wave                    | I — durable agent operations                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Primary owner           | Backend work-management domain                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Supporting owners       | AI backend execution adapter, backend facade, shared chat surface                                                                                                                                                                                                                                                                                                                                                                                                         |
| Informs                 | [I1 governed routines](./PRD-AR-I1-agent-proposed-routines-automation.md), [I2 persistent goals](./PRD-AR-I2-persistent-goals-bounded-continuation.md), [I4 lifecycle event subscriptions](./PRD-AR-I4-governed-agent-event-subscriptions.md)                                                                                                                                                                                                                             |
| Depends on              | [D2 builtins and subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md), [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md), [A4 effect stager](../../generative-surfaces-v2-1/prds/PRD-A4-effect-stager.md), [A5 commit coordinator](../../generative-surfaces-v2-1/prds/PRD-A5-commit-coordinator.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) |
| Primary success measure | Every ready item dispatches at most one logical run, survives service failure, and reaches an explainable terminal or blocked state                                                                                                                                                                                                                                                                                                                                       |

## Implementer brief

Read:

- `services/backend/src/backend_app/`
- `services/backend/src/backend_app/audit_reader.py`
- `services/backend/src/backend_app/routes/audit_list.py`
- `services/backend/src/backend_app/routes/audit_export.py`
- `services/ai-backend/src/agent_runtime/delegation/subagents/runner.py`
- `services/ai-backend/src/agent_runtime/delegation/subagents/atlas_task_tool.py`
- `services/ai-backend/src/agent_runtime/persistence/`
- `services/ai-backend/src/runtime_worker/`
- `services/ai-backend/src/runtime_api/`
- `services/ai-backend/src/runtime_adapters/`
- `services/backend-facade/src/backend_facade/`
- `packages/chat-surface/`

The backend owns product records, readiness, dependency state, authorization, and dispatch intent. A ready item creates an ordinary queued AI-backend run through authenticated HTTP and a durable outbox. The existing AI-backend run queue remains the only execution queue and keeps its current claim, lease, checkpoint, cancellation, and event semantics.

The asynchronous subagent lifecycle and persistent subagent records are execution internals. They may satisfy a run's item but are not the canonical product work-item store.

## Problem statement

Longer objectives need visible decomposition: what work exists, which items depend on others, what is ready, what is blocked, who owns a decision, and which result satisfied an item. A model-visible todo list or in-process subagent is not sufficient because it may disappear with a process, cannot serve as the product record, and does not provide durable authorization or retention.

Creating another AI-backend queue would be equally problematic. Runs already provide queued execution, worker claims, leases, checkpoints, cancellation, and events. Duplicating those mechanisms would introduce split ownership and ambiguous recovery.

The product therefore needs a backend-owned work-item DAG that records intent and readiness, then dispatches existing runs exactly once at the logical boundary.

## Current state and strengths to preserve

- AI backend already has a durable queued-run lifecycle, runtime workers, checkpoints, events, replay, and cancellation.
- Subagent execution already models authority attenuation, concurrency, persistent lineage, and streamed outcomes.
- Backend already owns tenant authorization, connector records, audit, and target product persistence.
- The facade and shared chat surface already expose pending activity without apps calling internal services.
- A3–A5 already govern consequential operations and ambiguous external outcomes.

## Objectives and outcomes

1. Persist work-item identity, immutable scope, dependencies, readiness, decisions, and result linkage in the backend.
2. Support bounded product-visible decomposition without exposing runtime internals.
3. Dispatch a ready item as one ordinary queued run using an idempotent authenticated API.
4. Reconcile lost dispatch responses without creating duplicate logical runs.
5. Project run and subagent progress back onto the item.
6. Pause on dependencies, approvals, missing input, policy denial, or budget exhaustion without occupying a worker.
7. Propagate cancellation and revocation to dispatched runs and dependent items.
8. Provide a governed work board with human or agent assignees, handoff, comments, visibility, and blocked/approval projections.

## Scope

- Work-item CRUD through owning products and immutable revisions
- Acyclic dependency graph, readiness evaluation, and block reasons
- Authority envelope, budget, deadlines, ownership, and input/result references
- Backend outbox and AI-backend run dispatch adapter
- Run/subagent event projection to item status
- Cancellation, retry intent, replacement, retention, deletion, and audit
- User-safe item and graph presentation
- Human and agent assignment, board/inbox views, handoff records, comments, watchers, and visibility policy

## Non-goals

- A second execution queue, worker claim loop, lease table, or checkpoint engine
- Replacing runs, goals, routines, approvals, model-visible todos, or subagent records
- A generic project-management or arbitrary workflow product
- Allowing models to write database state or modify DAGs directly
- Arbitrary code nodes, conditional scripts, or hidden recursive expansion
- Exactly-once external effects beyond the A5 contract

## Interfaces exposed

Facade routes:

```text
GET    /v1/work-items
GET    /v1/work-items/{work_item_id}
GET    /v1/work-items/{work_item_id}/graph
POST   /v1/work-items/{work_item_id}/pause
POST   /v1/work-items/{work_item_id}/resume
POST   /v1/work-items/{work_item_id}/cancel
POST   /v1/work-items/{work_item_id}/retry
POST   /v1/work-items/{work_item_id}/assign
POST   /v1/work-items/{work_item_id}/handoffs
POST   /v1/work-items/{work_item_id}/comments
GET    /v1/work-items/{work_item_id}/comments
POST   /v1/work-items/{work_item_id}/decisions/{decision_id}
```

Creation and graph mutation are exposed through the owning goal, routine, or run service contract, not a model-callable generic public endpoint.

Backend-to-AI-backend dispatch uses the existing run-creation contract with additions:

```text
POST /internal/v1/agent/runs
  origin = work_item
  origin_id = work_item_id
  origin_revision = work_item_revision
  dispatch_id
  authority_grant
  budget
  input_ref
  idempotency_key
```

AI-backend-to-backend projection consumes persisted lifecycle events or a dedicated authenticated event relay. It must be replayable and deduplicated.

## Interfaces consumed

- Verified backend identity, tenant membership, role, team, and resource visibility
- Backend connector registrations, credential references, audit, and durable approvals
- Existing AI-backend run create/read/cancel APIs and persisted lifecycle-event replay
- Existing AI-backend run queue, worker claims, leases, checkpoints, and subagent records
- A3–A5 governed operation and reconciliation contracts
- Governed artifact references for large inputs, evidence, comments, and results

## Core contracts

```text
AgentWorkItem
  work_item_id
  tenant_id
  owner_id
  assignee_type: human | agent | unassigned
  assignee_ref
  visibility: private | participants | team | tenant
  parent_id
  root_id
  origin: conversation | goal | routine | administrator
  origin_ref
  revision
  kind
  title
  objective
  completion_criteria[]
  input_refs[]
  authority_envelope
  connector_bindings[]
  budget
  priority
  not_before
  deadline
  status: draft | waiting_dependency | ready | dispatching |
          running | waiting_approval | blocked | succeeded |
          failed | cancelled | expired | superseded
  active_dispatch_id
  active_run_id
  result_refs[]
  terminal_reason
  created_at
  updated_at

WorkItemDependency
  predecessor_id
  successor_id
  policy: require_success | require_terminal | accept_partial
  satisfied_at
  satisfaction_ref

WorkItemDispatch
  dispatch_id
  work_item_id
  work_item_revision
  attempt
  idempotency_key
  authority_snapshot_ref
  status: pending | sent | accepted | running |
          terminal | cancel_requested | indeterminate
  run_id
  outbox_event_id
  last_event_sequence
  outcome_ref

WorkItemHandoff
  handoff_id
  work_item_id
  from_assignee
  to_assignee
  reason
  context_refs[]
  requested_at
  accepted_at
  status: pending | accepted | declined | cancelled

WorkItemComment
  comment_id
  work_item_id
  author_type: human | agent | system
  author_ref
  body_or_artifact_ref
  visibility
  created_at
  edited_at
```

The work item is product intent. The run is execution. `WorkItemDispatch` is a reconciliation record, not an execution lease.

## Invariants

- The dependency graph is acyclic and tenant-local.
- Scope, authority, dependencies, and budget are immutable within a revision.
- Child authority and allocated budget are strict subsets of the parent.
- At most one active dispatch exists per work-item revision.
- A dispatch idempotency key maps to one logical run.
- Only the backend changes canonical readiness and product status.
- AI backend remains authoritative for run execution state and event sequence.
- A terminal item cannot become active; retry creates a new dispatch attempt or replacement revision according to error class.
- Waiting or blocked items consume no AI-backend worker.

## Detailed design

### 1. Creation and decomposition

An assistant may propose a bounded decomposition, but trusted backend application logic validates and persists it only when allowed by the owning product. Validation requires:

- known item kind and version;
- explicit completion criteria;
- durable input references;
- authorized owner and tenant;
- bounded depth, fan-out, total nodes, and total allocated budget;
- authority and connector attenuation;
- valid deadlines and dependencies; and
- an idempotency key tied to the source plan revision.

Material changes create an immutable revision or replacement item. The model cannot mutate the graph through raw SQL, queue tools, or hidden messages.

### 2. Backend persistence

Implement PostgreSQL stores for items, immutable revisions, dependencies, decisions, dispatches, outcome projections, and outbox rows. The backend transaction that makes an item `ready` also records the pending dispatch intent when policy permits automatic dispatch.

Required indexes cover tenant/status, root/parent, ready time, deadline, active run, dispatch idempotency key, and predecessor/successor edges.

### 3. Readiness engine

Readiness is deterministic:

1. item and owning product are active;
2. `not_before` has passed and deadline has not;
3. required dependencies are satisfied;
4. required user decisions are resolved;
5. current owner, tenant policy, connector bindings, and budget remain valid; and
6. no active dispatch exists.

The engine reacts incrementally to dependency terminal events and policy changes. It does not rescan the complete graph after every update. Cycles are rejected on mutation using bounded reachability checks.

### 4. Dispatch without another queue

The backend outbox dispatcher calls the existing AI-backend internal run-creation API with `origin=work_item`. AI backend applies its existing queue transaction and returns `run_id`.

If the response is lost, the backend queries or retries with the same idempotency key. AI backend returns the existing logical run. The backend never claims work for execution and does not copy run leases into its work-item records.

I1 routine fires and I2 goal attempts may dispatch ordinary runs directly when no decomposition is needed. They reference I3 only when product-visible child work, dependencies, or decision routing are required.

### 5. Runtime execution adapter

AI backend assembles the immutable input references, short-lived authority grant, current policy snapshot, bounded budget, and completion criteria into an ordinary run. Existing runtime middleware enforces tool permissions, budgets, cancellation, checkpoints, and governed effects.

Subagents may execute within that run using existing bounded delegation. Their persistent records and events project onto the item but do not become backend DAG nodes unless a separately validated decomposition explicitly creates child items.

### 6. Lifecycle projection

AI-backend persisted events relay run acceptance, start, progress summary, approval wait, terminal outcome, cancellation, and relevant subagent lineage. The backend deduplicates by run and event sequence and updates the item projection transactionally.

Event loss is recovered from replay. Product status never depends on ephemeral streaming delivery.

### 7. Dependencies and decisions

On terminal projection, the backend evaluates direct successors in `O(out-degree)`. Dependency policy decides whether a predecessor failure blocks, allows terminal continuation, or admits a reviewed partial result.

User decisions use A4. The item references the durable approval; waiting releases all compute. Approval can authorize the already-scoped action but cannot widen item authority or mutate future siblings.

### 8. Work board, assignment, handoff, and comments

The shared work board presents items by `ready`, `running`, `waiting_approval`, `blocked`, and terminal state. Filters cover assignee, owner, goal/routine origin, deadline, and visibility. Agent assignees are stable governed profiles or runtime roles, never free-form model identities.

Assignment does not grant data or tool authority. The proposed assignee must already be allowed to view and perform the item's scope. A handoff is a durable request with a bounded context bundle, reason, and explicit accept/decline state. Until accepted, the prior assignee remains accountable unless policy explicitly transfers responsibility.

Comments are collaboration records, not harness instructions. Agent-authored comments contain safe summaries and evidence references, never hidden reasoning. Edits preserve revision history. Mentions and watchers create notifications but do not change authority. Approval and blocked states project onto the board with a direct path to the underlying durable decision or missing dependency.

Visibility is checked independently for the item, each comment, referenced artifacts, run detail, and approval. A broad item view must not disclose a restricted artifact or private comment.

### 9. Retry, replacement, and indeterminate dispatch

Transient run infrastructure failures may create a bounded new dispatch attempt. Deterministic validation, policy denial, cancellation, and exhausted budget do not auto-retry.

If run creation is ambiguous, the dispatch remains `indeterminate` until lookup by idempotency key resolves it. The backend cannot create a new idempotency key merely because a timeout occurred.

A changed objective, authority, inputs, dependency policy, or completion criterion creates a replacement revision, normally superseding and cancelling the prior active revision.

### 10. Cancellation and revocation

Cancelling a parent marks undispatched descendants cancelled, sends idempotent cancel requests for active runs, and prevents new dispatch. A dependent may instead become blocked when it also belongs to another authorized parent.

If a consequential operation may already have committed, A5 reconciliation determines the final effect state. UI wording distinguishes item cancellation from external-effect rollback.

## Ownership and service boundaries

| Responsibility                                                                          | Owner                 |
| --------------------------------------------------------------------------------------- | --------------------- |
| Work-item DAG, board, assignment, handoff, comments, readiness, dispatch, authorization | Backend               |
| Public product API aggregation                                                          | Backend facade        |
| Run queue, claims, leases, checkpoints, model execution, cancellation, events           | AI backend            |
| Runtime subagent lifecycle and records                                                  | AI backend            |
| Item/graph/pending-decision presentation                                                | Shared chat surface   |
| Consequential operations                                                                | Existing A3–A5 owners |

No deployable imports another service's source. Backend does not implement run leasing. AI backend does not become the canonical product work-item store.

## Persistence, retention, and deletion

- Backend PostgreSQL stores the canonical DAG, revisions, decisions, dispatches, and projections.
- AI backend retains runs, events, checkpoints, subagent records, and tool invocations under existing policy.
- Large inputs and results use governed artifact references and digests.
- Deletion traverses graph edges, dispatch/outbox rows, run linkage, approvals, result references, notifications, and derived summaries.
- Cross-store deletion is idempotent and reports partial failure until reconciled.
- Legal hold preserves required records while disabling further dispatch.

## Authentication, authorization, security, and audit

- Verified identity determines tenant and actor; no body-supplied identity is trusted.
- Backend checks graph visibility, ownership, delegation, connector scope, policy, and budget.
- Dispatch grants are signed, short-lived, item/revision/run-purpose-bound, and narrower than current actor authority.
- AI backend verifies the grant before queue admission and again through runtime middleware.
- Item text and upstream artifacts remain untrusted model input.
- Audit covers propose, create, revise, assign, handoff, comment, visibility change, dependency change, ready, dispatch, reconcile, decision, block, retry, cancel, supersede, complete, export, and delete.
- Per-root node, depth, fan-out, cost, deadline, and dispatch-attempt limits are mandatory.

## Performance and capacity budgets

- Work-item list/read: p95 under 300 ms.
- Board query with filters and pagination: p95 under 400 ms.
- Comment or handoff mutation: p95 under 300 ms excluding notifications.
- Ready-state transaction and outbox creation: p95 under 250 ms.
- Ready-to-run acceptance: p95 under 5 seconds under normal load.
- Cancellation request propagation to AI backend: p95 under 5 seconds.
- Direct dependency completion processing: `O(out-degree)` with a configured fan-out ceiling.
- Ready scans use `(status, not_before, priority)` indexes: `O(log W + B)` for `W` items and batch `B`.
- Graph reads are paginated and depth-bounded; interaction paths never materialize an unbounded DAG.

AI-backend claim, lease, checkpoint, and execution SLOs remain defined by the existing run subsystem rather than duplicated here.

## Failure, idempotency, and recovery

- Item creation, graph mutation, decisions, dispatch, cancel, and retry require idempotency keys.
- Ready-state and dispatch-outbox creation are atomic in the backend.
- Run creation is idempotent on tenant plus dispatch ID.
- Outbox delivery is at-least-once; AI backend returns the existing run on duplicate dispatch.
- Lifecycle projections deduplicate on run ID and event sequence.
- Lost events recover through AI-backend replay from the last projected sequence.
- Backend or AI-backend outage leaves a visible pending state and resumes safely.
- Policy, audit, or durable-store outage prevents new dispatch but not read/export.

## Observability and quality gates

Metrics:

- items by status, root, origin, and tenant;
- ready age and dispatch latency;
- dispatch duplicates, retries, and indeterminate duration;
- lifecycle projection lag and replay count;
- dependency block reasons and decision latency;
- assignment age, pending handoffs, comments, and visibility denials;
- graph depth/fan-out and rejected decompositions;
- cancellation propagation;
- runs, tool calls, cost, and outcomes per item; and
- item completion versus user correction.

Trace lineage is `origin → work_item/revision → dispatch_id → run_id → subagent/tool/operation → result`.

Release gates:

- one logical run per dispatch under timeout and retry tests;
- no backend run-claim or lease implementation;
- no canonical product work-item tables in AI backend;
- stale or forged grants cannot queue runs;
- dependency and cancellation projections recover from replay;
- every visible item has owner, origin, budget, criteria, and terminal reason; and
- tenant isolation, retention, deletion, audit, and legal-hold tests pass.

## Rollout and backout

1. Ship backend schema and read-only item projections.
2. Create work items from one internal goal flow with dispatch disabled.
3. Enable idempotent run dispatch for allowlisted tenants.
4. Project run lifecycle from replayable events and validate against run state.
5. Add dependencies, decisions, cancellation, and bounded decomposition.
6. Add board views, assignment, handoff, comments, watchers, and approval/block projection.
7. Allow I1/I2 to reference items only where visible decomposition is useful.

Backout stops new dispatch outbox delivery, preserves the DAG and status history, and leaves existing AI-backend runs governed by their ordinary lifecycle. Active runs may be cancelled explicitly. No run queue migration is needed.

## Implementation slices

1. Backend work-item/revision/dependency/dispatch schema and ports
2. Readiness, graph validation, authority attenuation, and budgets
3. Transactional dispatch outbox and idempotent AI-backend run creation
4. Run lifecycle relay, projection, replay, and reconciliation
5. Decisions, retry, cancellation, replacement, and dependent updates
6. Facade contracts, board/item/graph UI, assignment, handoff, and comments
7. Retention, deletion, audit export, and operator diagnostics
8. Fault-injection, quality evaluation, and staged producer adoption

## Test plan

- Unit: DAG cycles, readiness, attenuation, budget allocation, dependency policies
- Store contract: revisions, unique active dispatch, indexes, deletion, legal hold
- Integration: ready item through queued run and terminal projection
- Concurrency: duplicate readiness events and outbox dispatchers
- Fault injection: timeout before/after run acceptance and event projection
- Security: cross-tenant edges, forged grant, stale policy, excessive fan-out
- Governance: approval wait, effect reconciliation, owner revocation
- Collaboration: assignment authorization, handoff races, comment revision, visibility boundaries
- Recovery: event replay after projection outage and cancel retry
- Load: large bounded graphs, many ready items, incremental successor evaluation

## Definition of done

- Backend durably owns work-item intent, dependencies, readiness, decisions, and dispatch records.
- A ready item creates one ordinary queued run through an idempotent internal contract.
- AI backend remains the sole owner of execution claims, leases, checkpoints, and runtime events.
- Product status recovers from persisted event replay and survives either service restarting.
- Human and agent assignees can hand off work, collaborate through governed comments, and see blocked/approval state without widening access.
- Cancellation and revocation prevent new dispatch and reach active runs.
- No item can expand authority, budget, scope, depth, or fan-out without validated revision.
- Security, retention, deletion, audit, load, and fault-injection gates pass.

## Guardrails

- Work items describe durable product intent; runs execute it.
- Do not build another execution queue.
- Do not mirror run leases or checkpoints into backend product state.
- Models may propose decomposition but cannot persist or dispatch it directly.
- Children only narrow authority and allocated budget.
- No busy waiting, hidden retry, unbounded graph, or completion without evidence.
- Invalid owner, policy, audit, grant, or durable store fails closed for dispatch.

## Open decisions

1. Which owning product first creates product-visible work items.
2. Whether a failed item retry is a new dispatch or always a new item revision for user visibility.
3. Which run progress events are safe and useful to project at item level.
4. Maximum DAG nodes, depth, fan-out, and concurrent ready items by tenant tier.
5. Whether cross-goal dependency references are supported after the first release.
