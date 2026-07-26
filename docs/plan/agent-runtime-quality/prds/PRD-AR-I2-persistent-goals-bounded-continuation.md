# PRD-AR-I2 — Persistent goals with bounded continuation

**Goal:** Let users entrust multi-session objectives to the product while keeping every continuation time-bounded, budget-bounded, authority-bounded, observable, and interruptible.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Wave                    | I — durable agent operations                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Primary owners          | Backend goals domain, AI backend execution                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Supporting owners       | Backend facade, runtime worker, chat surface                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Depends on              | [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md), [A4 effect stager](../../generative-surfaces-v2-1/prds/PRD-A4-effect-stager.md), [A5 commit coordinator](../../generative-surfaces-v2-1/prds/PRD-A5-commit-coordinator.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md), [F1 evaluation](./PRD-AR-F1-harness-observability-evaluation-promotion.md), [F4 tool-use controller](./PRD-AR-F4-task-aware-tool-use-controller.md) |
| Optional integration    | [I3 durable work items](./PRD-AR-I3-durable-agent-work-items.md) when a goal needs product-visible decomposition                                                                                                                                                                                                                                                                                                                                                                                                                |
| Primary success measure | Goals make measurable progress across sessions without exceeding an approved budget or continuing after pause, revocation, or block                                                                                                                                                                                                                                                                                                                                                                                             |

## Implementer brief

Read:

- `services/ai-backend/src/agent_runtime/execution/`
- `services/ai-backend/src/agent_runtime/persistence/`
- `services/ai-backend/src/runtime_worker/`
- `services/ai-backend/src/agent_runtime/delegation/`
- `services/backend/src/backend_app/`
- `services/backend-facade/src/backend_facade/`
- `packages/chat-surface/`
- I3 where decomposition is required, A3–A5, and E1

There is no first-class durable goal aggregate today. Do not model a goal as a long-running HTTP request, a conversation flag, a recurring prompt, or a recursively self-spawning agent. The backend owns the product record and authorization; the AI backend executes finite attempts using ordinary runs and checkpoints.

## Problem statement

Some objectives cannot be completed in one conversation or one run: investigate a changing issue, gather evidence over several days, coordinate a staged migration, or work through a large corpus. Conversations and runs provide durable history, but they do not define a persistent objective with measurable success criteria, approved resource limits, scheduled continuation, progress checkpoints, and an explicit terminal state.

Without a first-class contract, continuation tends toward one of two unsafe forms: hidden infinite autonomy or fragile client-side reminders. The product needs a governed middle ground: a durable goal that can schedule finite attempts, stop when blocked, request decisions, and resume only within its approved envelope.

## Current state and strengths to preserve

- Queued runs, persisted events, checkpoints, replayable streams, cancellation, and worker claims provide finite execution units.
- Tool permissions, connector scope, approvals, and governed effects provide action-level control.
- Subagent records and task linkage provide execution lineage for delegated work.
- Backend-owned scheduled jobs can provide durable wakeups independently of routine activation.
- The shared chat surface can present pending work, approvals, activity, and outcomes across web and desktop.

## Objectives and outcomes

1. Create a durable goal with explicit objective, success criteria, constraints, authority, and budgets.
2. Break progress into finite attempts with a maximum duration and work allowance.
3. Persist evidence-backed progress checkpoints and next-action rationale.
4. Pause automatically on ambiguity, exhausted budget, lost permission, repeated failure, or required approval.
5. Let authorized users inspect, amend, resume, cancel, or archive a goal.
6. Prevent concurrent attempts and continuation after revocation.
7. Measure useful progress, resource consumption, and user correction.

## Scope

- Goal CRUD and immutable revisions
- Success criteria, constraints, budgets, deadlines, and continuation policy
- Finite attempt scheduling and execution
- Progress ledger, blockers, decisions, and evidence references
- Pause/resume/cancel/archive and budget-extension review
- Goal status projection in chat and a dedicated pending-work view
- Retention, deletion, audit, and cost attribution

## Non-goals

- An always-running autonomous process
- Self-defined authority, budget, deadline, or success criteria
- A project-management suite, ticket tracker, or generic DAG engine
- Unbounded recursive subagents
- Automatic purchase, publication, deployment, or communication outside A3–A5
- Claiming objective completion from model confidence alone

## Interfaces consumed

- Verified backend identity, membership, roles, tenant policy, and budget policy
- Backend connector registrations and vault-backed credential references
- Existing AI-backend run creation, queued execution, checkpoints, cancellation, and persisted events
- A3–A5 classification, approvals, commit, and reconciliation for consequential actions
- E1 retention, deletion, legal hold, audit export, and lifecycle controls
- I3 only when a goal explicitly creates product-visible child items or dependencies

## Interfaces exposed

Facade routes:

```text
POST   /v1/goals
GET    /v1/goals
GET    /v1/goals/{goal_id}
POST   /v1/goals/{goal_id}/revisions
POST   /v1/goals/{goal_id}/pause
POST   /v1/goals/{goal_id}/resume
POST   /v1/goals/{goal_id}/cancel
POST   /v1/goals/{goal_id}/archive
POST   /v1/goals/{goal_id}/budget-requests/{request_id}/decide
GET    /v1/goals/{goal_id}/attempts
GET    /v1/goals/{goal_id}/progress
```

Goal attempts dispatch through the existing authenticated AI-backend run-creation route:

```text
POST /internal/v1/agent/runs
  origin = goal
  origin_id = goal_id
  origin_revision = goal_revision
  attempt_id
  idempotency_key
  authority_grant
  attempt_budget
```

## Core contracts

```text
Goal
  goal_id
  tenant_id
  owner_id
  revision
  title
  objective
  success_criteria[]
  constraints[]
  authority_envelope
  connector_bindings[]
  total_budget
  attempt_budget
  deadline
  continuation_policy
  status: draft | active | paused | blocked |
          achieved | cancelled | expired | archived
  next_wake_at
  policy_revision
  created_at
  updated_at

GoalAttempt
  attempt_id
  goal_id
  goal_revision
  ordinal
  trigger: user | scheduled | dependency | event
  status: planned | dispatching | running | waiting_approval |
          blocked | succeeded | failed | cancelled | expired
  run_id
  started_at
  deadline_at
  consumed_budget
  checkpoint_id
  terminal_reason

GoalCheckpoint
  checkpoint_id
  attempt_id
  progress_claims[]
  evidence_refs[]
  criteria_assessments[]
  completed_work[]
  remaining_work[]
  blockers[]
  next_action
  confidence
  budget_snapshot
  policy_snapshot_ref
  created_at
```

Budgets include wall-clock duration, model tokens or cost, tool calls, subagent count, external-operation count, and attempt count. Tenant policy may impose stricter values.

## State machine and invariants

- `draft → active` requires explicit confirmation of objective, success criteria, budgets, and authority.
- `active → paused|blocked|achieved|cancelled|expired`.
- `paused|blocked → active` requires an authorized resume decision and a fresh policy check.
- Terminal states cannot return to active; a new goal may be cloned from them.
- At most one attempt may be `dispatching|running|waiting_approval` per goal.
- Only the backend changes goal status. AI backend reports structured attempt outcomes.
- `achieved` requires all mandatory criteria to be supported by accepted evidence rules or explicit owner confirmation.
- An attempt cannot change the goal revision, authority envelope, or total budget.

## Detailed design

### 1. Goal creation and confirmation

The assistant may draft a goal from a conversation, but the user reviews a structured confirmation showing objective, success criteria, non-goals, dependencies, cadence, deadline, maximum cost, writable destinations, connector use, and expected approval points.

The backend validates that authority and connector bindings are a subset of the actor's current rights. Ambiguous success criteria remain draft-only.

### 2. Continuation planner

Each attempt begins with a compact goal dossier: immutable goal revision, latest accepted checkpoint, unresolved blockers, relevant evidence references, remaining budget, current policy grant, and reason for waking. It does not replay every prior attempt.

The model proposes an `AttemptPlan` containing bounded tasks, expected evidence, tool categories, estimated budget, and stop conditions. Runtime middleware rejects plans that exceed the attempt grant.

### 3. Finite execution

An attempt is an ordinary queued run with `origin=goal`, an absolute deadline, maximum model turns, tool calls, subagents, and cost. Existing run cancellation and checkpointing remain authoritative.

At 80% of any attempt budget, the runtime must checkpoint and choose one of: complete, produce a partial result, request a decision, or stop blocked. It cannot silently request another attempt.

### 4. Progress ledger

The AI backend emits structured checkpoint candidates. The backend validates schema and stores append-oriented checkpoints linked to evidence. Facts, artifacts, test results, and external receipts are references; ungrounded narrative is marked as an assessment, not evidence.

The latest checkpoint may supersede an earlier assessment but cannot delete history. Contradictions are retained and surfaced.

### 5. Wake policy

A goal can wake from a user resume, a bounded schedule, an approved event subscription, a resolved approval, or a completed dependency. The backend calculates `next_wake_at`; it must not depend on an open client.

Continuation policies include:

- `manual_only`;
- `scheduled(max_attempts, minimum_interval)`;
- `on_dependency(max_attempts)`; and
- combinations explicitly approved at activation.

Backend-owned scheduled jobs supply wakeups independently of I1. I4 may publish goal lifecycle events outward, but it does not wake or control a goal.

### 6. Blocking and escalation

Mandatory block reasons include missing information, contradictory requirements, permission revoked, connector unavailable, approval required, budget exhausted, deadline reached, repeated failure, unsafe content, and no measurable progress.

A budget-extension request states what was attempted, evidence gained, why more work is justified, requested increments, and alternatives. No extension takes effect until an authorized decision.

### 7. Completion

Completion evaluation maps evidence to each success criterion. Criteria may be:

- machine-verifiable;
- artifact-review-required;
- external-receipt-backed; or
- owner-confirmed.

The goal reaches `achieved` only when every required criterion satisfies its configured evaluator. Otherwise the attempt succeeds as progress while the goal stays active, paused, or blocked.

### 8. Amendments

Material changes to objective, criteria, authority, connectors, deadline, or budgets create a new immutable goal revision and require confirmation. In-flight attempts retain their original revision and are normally cancelled before the new revision activates.

## Ownership and service boundaries

| Responsibility                                                      | Owner                                    |
| ------------------------------------------------------------------- | ---------------------------------------- |
| Goal aggregate, revisions, policy, budgets, schedule, authorization | Backend                                  |
| Public product API                                                  | Backend facade                           |
| Attempt planning, finite execution, checkpoint candidate generation | AI backend                               |
| Run and subagent orchestration                                      | AI backend worker                        |
| Goal and decision UI                                                | Shared chat surface through facade ports |

Cross-service messages use authenticated internal HTTP and transactional outboxes. The backend does not import runtime code; the AI backend does not become the source of truth for product ownership or budget authorization.

## Persistence, retention, and deletion

- PostgreSQL stores goals, revisions, attempts, checkpoints, decisions, budgets, and audit linkage.
- Large artifacts and evidence remain in the governed artifact repository; goals retain typed references and digests.
- User and tenant deletion cascade through attempts, run references, notifications, and derived summaries.
- Legal hold preserves immutable revisions and evidence links while disabling further execution.
- Archive removes a goal from active views but does not change retention.
- Checkpoint compression may create a new summary record; it never destroys source checkpoint lineage before policy permits.

## Authentication, authorization, security, and audit

- Verified identity determines tenant and actor; request fields cannot override them.
- Owner and delegated roles are explicit per action.
- Attempt grants are signed, short-lived, revision-bound, and narrower than current user and tenant policy.
- Permissions, connector access, destination allowlists, and budgets are re-resolved before run dispatch and before consequential operations.
- Goal text and retrieved evidence are untrusted data subject to prompt-injection defenses.
- Audit events cover create, confirm, amend, wake, dispatch, checkpoint, block, approval, budget request/decision, pause, resume, cancel, achieve, archive, export, and delete.
- Revocation prevents new dispatch immediately and propagates cancellation to active runs.

## Performance and capacity budgets

- Goal list/read: p95 under 300 ms.
- Ready-attempt transaction and outbox creation: p95 under 250 ms.
- Ready-attempt to queued-run acceptance: p95 under 5 seconds under normal load.
- Goal dossier assembly: p95 under 500 ms excluding artifact retrieval.
- Scheduler wake lag: p95 under 30 seconds.
- Revocation visible to the runtime: p95 under 5 seconds.
- Dossier selection is `O(C log E)` or better for `C` checkpoint/evidence candidates, with a hard selected-item and token cap; attempt startup must not be `O(total conversation history)`.
- Tenant concurrency and daily cost are enforced before model invocation.

## Failure, idempotency, and recovery

- Goal and attempt mutations require idempotency keys and optimistic revisions.
- Run dispatch is idempotent on `attempt_id`; a lost response is reconciled by `attempt_id` and `run_id` before re-dispatch.
- A checkpoint write is idempotent by attempt and checkpoint sequence.
- Scheduler or worker outage never advances the goal state without a durable record.
- Approval timeout moves the attempt to blocked or expired according to policy; it does not hold a worker.
- Repeated failure uses bounded exponential backoff and then blocks.
- If audit, policy, budget, or durable-store dependencies are unavailable, new work fails closed.

## Observability and evaluation

Metrics:

- goals created, confirmed, active, blocked, achieved, cancelled, and expired;
- time to first progress and time to terminal state;
- attempts per goal and no-progress attempt rate;
- budget utilization and extension decisions;
- block reasons and user correction rate;
- evidence coverage per success criterion;
- concurrent-attempt invariant violations;
- wake lag, dispatch reconciliation, and duplicate dispatch attempts; and
- revocation propagation.

Trace lineage is `goal_id/revision → attempt_id → run_id → subagent_id/tool_call_id/operation_id → checkpoint_id`. Logs redact goal content, evidence bodies, credentials, and model chain-of-thought.

Quality evaluation uses scenario suites for long-horizon research, multi-stage implementation, permission loss, contradictory evidence, deadline expiry, and user amendment. A goal receives no completion credit without criterion-level evidence.

## Rollout and backout

1. Ship schema, APIs, and read-only UI with execution disabled.
2. Enable manual-only goals for internal tenants.
3. Add one-attempt execution with strict low budgets.
4. Enable scheduled continuation for allowlisted tenants.
5. Add dependency wakeups and optional I3 decomposition after their reliability gates.
6. Expand tenant limits based on completion quality and safety data.

Backout globally prevents new attempt dispatches, preserves goal records and checkpoints, and exposes pause/cancel/export. Active runs are cancelled or allowed to checkpoint according to incident policy.

## Implementation slices

1. Goal/revision/attempt/checkpoint schema and PostgreSQL store
2. Facade contracts and create/review/manage UI
3. Transactional attempt dispatch outbox and execution grant
4. Goal dossier and bounded attempt planner
5. Checkpoint/evidence ledger and criterion evaluators
6. Scheduler wake integration and block notifications
7. Budget extension, amendment, retention, and audit
8. Evaluation suites, dashboards, and tenant rollout

## Test plan

- Unit: state transitions, policy narrowing, budgets, evaluator outcomes, dossier selection
- Store contract: revision conflicts, unique active attempt, deletion, legal hold
- Integration: create through multi-attempt achievement
- Concurrency: scheduler replicas, duplicate wake delivery, and duplicate outbox dispatch
- Fault injection: lost dispatch response, checkpoint timeout, and worker crash
- Security: cross-tenant access, stale membership, connector revocation, forged grant
- Governance: budget exhaustion, extension rejection, approval timeout, effect reconciliation
- Quality: no-progress detection, contradiction retention, evidence-backed completion
- Load: many dormant goals and indexed due-wake scans

## Definition of done

- Users can define, confirm, inspect, pause, resume, cancel, and archive a goal.
- Every attempt is finite, durably recorded, idempotently dispatched, budgeted, revision-bound, and traceable.
- Continuation survives restart but stops on revocation, exhausted limits, or blocking ambiguity.
- Progress and completion claims link to reviewable evidence.
- No goal can self-expand its authority, budget, success criteria, or continuation policy.
- Retention, deletion, audit export, tenant isolation, and legal-hold tests pass.

## Guardrails

- Persistent does not mean continuously running.
- The model may propose; only authorized policy and user decisions grant continuation.
- Silence is never approval of a budget, scope, deadline, or effect.
- No recursive self-replication or unbounded subagent spawning.
- No completion based solely on a narrative assertion.
- No execution while the goal, owner, tenant, connector, policy, audit, or store is invalid.

## Open decisions

1. Which success-criterion evaluators ship in the first release.
2. Default maximum attempts, attempt duration, and minimum wake interval.
3. Whether shared goals support multiple approvers or only an owner plus administrators.
4. Whether deadline extension is always material or can be policy-preapproved.
5. Which goal state and evidence fields are exportable to external project systems.
