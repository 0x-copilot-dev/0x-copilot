# PRD-AR-I1 — Agent-proposed routines and governed automation

**Goal:** Turn recurring work recognized during ordinary conversations into durable, reviewable routines that execute reliably without granting the model authority to create or expand automation on its own.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Status                  | Proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Wave                    | I — durable agent operations                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Primary owners          | Backend routines domain, AI backend runtime worker                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Supporting owners       | Backend facade, shared API contracts, chat surface                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Depends on              | [H5 post-run learning candidates](./PRD-AR-H5-post-run-learning-candidate-pipeline.md), [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md), [A4 effect stager](../../generative-surfaces-v2-1/prds/PRD-A4-effect-stager.md), [A5 commit coordinator](../../generative-surfaces-v2-1/prds/PRD-A5-commit-coordinator.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) |
| Primary success measure | Accepted routine proposals reach exactly one valid scheduled fire without hidden privilege expansion or duplicate execution                                                                                                                                                                                                                                                                                                                                        |

## Implementer brief

Read these code paths before implementation:

- `services/backend/src/backend_app/routines/`
- `services/backend/src/backend_app/app.py`
- `services/ai-backend/src/agent_runtime/api/routine_backend_client.py`
- `services/ai-backend/src/agent_runtime/api/routine_permission_check.py`
- `services/ai-backend/src/runtime_worker/jobs/routine_scheduler.py`
- `services/ai-backend/src/runtime_worker/jobs/routine_pre_fire_gate.py`
- `services/ai-backend/src/runtime_worker/jobs/proposal_extractor.py`
- `services/ai-backend/src/runtime_worker/__main__.py`
- `services/backend-facade/src/backend_facade/`
- the H5 and A3–A5 PRDs linked above

Preserve the existing routine API, trigger model, pre-fire policy evaluation, and typed audit/event conventions where they satisfy this contract. Replace the in-memory-only production path and connect the scheduler through service APIs; do not move backend-owned routine records into the AI backend.

## Problem statement

Users frequently repeat research, reporting, monitoring, and administrative workflows. The runtime can recognize routine-shaped learning candidates, and the backend already models routines, triggers, fires, audit rows, and webhook entry points. However, routine persistence defaults to an in-memory adapter, the worker scheduler is not composed into the production worker lifecycle, and post-run routine candidates do not yet flow through a user review and activation path.

The product therefore cannot promise that an accepted recurring workflow will survive restart, fire on time, re-check authority, or execute exactly once. Direct model-created schedules would also be unsafe: a conversational suggestion must not silently become a durable background capability.

## Current state and strengths to preserve

- Backend-owned routine CRUD, trigger, fire, webhook, and audit concepts establish the correct service boundary.
- The AI backend scheduler includes cron/recurrence handling, missed-fire behavior, and a pre-fire permission gate.
- The H5 proposal pipeline defines evidence-backed, consent-aware routine candidates rather than immediately active automation.
- Run events, resumable streams, cancellation, approvals, and tenant-scoped identity already provide execution primitives.
- The operation gateway and governed-effects protocol remain the authority for consequential actions performed inside a routine run.

## Objectives and outcomes

1. Present evidence-backed routine proposals for explicit user or administrator review.
2. Compile an accepted proposal into a versioned routine with a bounded authority envelope.
3. Persist routine definitions, revisions, schedules, fire claims, and outcomes durably.
4. Start the scheduler as a supported worker responsibility with leases and health signals.
5. Guarantee idempotent fire creation and at-most-one active execution per fire.
6. Re-evaluate identity, permissions, connector access, policy, and budget immediately before every fire.
7. Make pause, resume, edit, revoke, retry, and delete behavior visible and auditable.

## Scope

- Candidate-to-routine review and activation
- Cron, supported recurrence-rule, and authenticated webhook triggers
- Durable PostgreSQL routine and fire adapters
- Scheduler composition, leasing, misfire policy, and recovery
- Routine revisioning and effective-policy snapshots
- Trigger-to-run dispatch and outcome projection
- User-facing status, next fire, last outcome, pause, and revoke controls

## Non-goals

- Allowing a model to activate a routine without an authorized human decision
- A general-purpose workflow language or arbitrary user-supplied code
- Reimplementing approval or governed-effect semantics from A3–A5
- Guaranteeing exactly-once behavior for an external service that lacks idempotency support
- Treating every post-run learning candidate as a routine
- Supporting seconds-level real-time scheduling in the first release

## Interfaces consumed

- H5 `LearningCandidate` records of kind `routine`
- Verified backend identity, tenant, role, and policy context
- Backend connector registrations and credential references
- AI backend run creation, cancellation, events, and final outcomes
- A3–A5 operation classification, approval, prepare/commit, and reconciliation
- E1 audit, retention, export, legal-hold, and deletion requirements

## Interfaces exposed

Product-facing routes are facade-only:

```text
GET    /v1/routine-proposals
GET    /v1/routine-proposals/{proposal_id}
POST   /v1/routine-proposals/{proposal_id}/accept
POST   /v1/routine-proposals/{proposal_id}/reject
GET    /v1/routines
POST   /v1/routines/{routine_id}/pause
POST   /v1/routines/{routine_id}/resume
POST   /v1/routines/{routine_id}/revisions
DELETE /v1/routines/{routine_id}
GET    /v1/routines/{routine_id}/fires
POST   /v1/routine-fires/{fire_id}/retry
```

AI-backend-only internal routes:

```text
POST /internal/v1/routine-fires/claim
POST /internal/v1/routine-fires/{fire_id}/heartbeat
POST /internal/v1/routine-fires/{fire_id}/dispatch
POST /internal/v1/routine-fires/{fire_id}/complete
POST /internal/v1/routine-fires/{fire_id}/release
```

Internal calls require the enterprise service token plus explicit organization and user identity headers. They are not proxied by the facade.

## Core contracts

```text
RoutineProposalDecision
  proposal_id
  proposal_version
  decision: accepted | rejected
  decided_by
  decided_at
  title
  objective
  trigger_spec
  input_template
  authority_envelope
  connector_bindings[]
  budget
  misfire_policy
  evidence_refs[]
  idempotency_key

RoutineRevision
  routine_id
  revision
  status: draft | active | paused | revoked | deleted
  trigger_spec
  objective
  input_template
  authority_envelope
  connector_bindings[]
  budget
  policy_revision
  created_by
  approved_by
  effective_at

RoutineFire
  fire_id
  routine_id
  routine_revision
  scheduled_for
  dedupe_key
  status: pending | claimed | dispatched | running |
          waiting_approval | succeeded | failed | skipped | cancelled
  lease_owner
  lease_expires_at
  run_id
  policy_snapshot_ref
  attempt
  outcome_ref
```

`dedupe_key` is unique per tenant, routine, active revision, and logical trigger occurrence. The create-fire transaction must use a unique constraint, not a read-before-write check.

## Detailed design

### 1. Proposal review

H5 routes a routine candidate to the backend review inbox. The review displays the source evidence, proposed objective, schedule in the user's timezone, connector dependencies, estimated cost, authority envelope, and examples of actions that may require approval.

Accepting a proposal requires an explicit idempotency key and an optimistic proposal version. Any material edit to scope, connectors, recipients, writable destinations, budget, or cadence creates a new draft requiring confirmation. Rejection records a reason category but does not delete the source evidence before its retention deadline.

### 2. Compilation and validation

The backend compiles the accepted proposal into a closed routine schema. It rejects:

- unbounded or ambiguous recurrence;
- shell text, executable code, or arbitrary expressions;
- connectors not installed for the tenant;
- secret values instead of credential references;
- authority wider than the reviewer possesses;
- budgets above tenant policy; and
- webhook triggers without a signing configuration and replay window.

The schedule preview must show at least the next three occurrences and daylight-saving behavior before activation.

### 3. Durable persistence

Implement a PostgreSQL `RoutinesStore` for definitions, immutable revisions, triggers, fires, decisions, and audit linkage. Mutations use transactions and optimistic revision checks. Routine deletion is a tombstoned state until dependent fires and legal-hold requirements permit physical deletion.

The in-memory adapter remains test/dev-only and must not be selected when the service declares a production environment.

### 4. Scheduler lifecycle

Compose `RoutineSchedulerLoop` into the runtime worker entry point behind a feature flag. Each scheduler replica:

1. asks the backend for due fire claims;
2. claims rows with database locking and a bounded lease;
3. heartbeats while dispatching;
4. releases claims on transient failure; and
5. records terminal skip reasons for permanent policy failures.

Horizontal replicas may scan concurrently. Correctness comes from transactional claims and unique dedupe keys, not leader election.

### 5. Pre-fire revalidation

Immediately before dispatch, the backend re-resolves:

- routine and owner status;
- current tenant membership;
- current role and policy revision;
- connector installation, scope, and credential availability;
- destination allowlists;
- remaining daily/monthly budget;
- concurrent-fire limits; and
- any required administrator hold.

The runtime receives a signed, short-lived execution grant referencing the routine revision and policy snapshot. It must not infer authority from the original conversation.

### 6. Run dispatch

Every fire creates an ordinary queued agent run with `origin=routine`, stable `fire_id`, immutable routine revision, bounded input, and authority envelope. The run uses existing checkpoints, events, approvals, and cancellation.

Consequential tool calls remain governed by A3–A5. A user may approve an individual fire operation, but the approval does not widen future routine authority unless a separate routine revision is accepted.

### 7. Misfires, overlap, and retries

Supported misfire policies are `skip`, `run_once_now`, and `catch_up_bounded(max_occurrences)`. The default is `run_once_now` with one catch-up fire. Routine overlap defaults to `forbid`; optional `queue_one` coalesces occurrences while a prior fire is active.

Retry creates a new attempt under the same fire and dedupe key. It may not create a second successful run after a committed result. External operations rely on A5 idempotency and reconciliation.

### 8. Editing, pause, revoke, and deletion

Edits create a new immutable revision. Existing in-flight fires retain their original revision and policy snapshot unless revoked by an administrator. Pause prevents new claims. Revoke cancels unstarted fires and requests cancellation of active runs. Deletion follows E1 cascade and legal-hold rules.

## Ownership and service boundaries

| Responsibility                                                          | Owner                                 |
| ----------------------------------------------------------------------- | ------------------------------------- |
| Proposal review, routine records, schedules, tenant policy, credentials | Backend                               |
| Public API aggregation                                                  | Backend facade                        |
| Scheduler scan, run dispatch, model execution                           | AI backend                            |
| Fire and run presentation                                               | Chat surface through facade contracts |
| Operation approvals and external commit                                 | Existing A3–A5 owners                 |

No deployable imports another service's source. Cross-service integration is authenticated HTTP with versioned contracts.

## Persistence, retention, and deletion

- Retain immutable revisions and decision audit metadata according to tenant policy.
- Store prompt-sized routine inputs separately from large evidence and results, using durable references.
- Cascade user/tenant deletion through proposal decisions, routines, pending fires, run linkage, and notification rows.
- Legal hold blocks physical deletion while hiding revoked routines from scheduling.
- Audit export must include revision, reviewer, policy snapshot, fire state changes, run ID, approvals, and terminal outcome.

## Authentication, authorization, security, and audit

- Derive tenant and actor only from verified tokens.
- Require owner or delegated administrator authority for activation and material edits.
- Never persist plaintext connector credentials in a routine.
- Verify webhook signatures, timestamp windows, nonce replay protection, body-size limits, and tenant routing.
- Treat proposal evidence and webhook content as untrusted data, not harness instructions.
- Emit append-oriented audit events for propose, view-sensitive-evidence, accept, reject, activate, edit, pause, resume, fire, skip, retry, revoke, and delete.
- Rate-limit proposals, webhook delivery, manual retry, and per-routine concurrent fires.

## Performance and capacity budgets

- Routine list/read: p95 under 250 ms excluding facade network overhead.
- Proposal acceptance and durable activation: p95 under 750 ms.
- Due-fire claim transaction: p95 under 200 ms for batches up to 100.
- Scheduled dispatch lag: p95 under 30 seconds and p99 under 90 seconds.
- Pre-fire policy evaluation: p95 under 500 ms excluding an OAuth refresh.
- Scheduler scan cost: `O(log F + B)` with an index on next due time, where `F` is pending fires and `B` is claimed batch size.
- Fire creation and claim must not scan conversation history.

## Failure, idempotency, and recovery

- All mutation routes require idempotency keys and return the original result on retry.
- Expired claims become reclaimable after a jittered safety window.
- Dispatch writes `run_id` transactionally or through an outbox so a crash cannot silently lose the fire.
- If dispatch response is lost, reconcile by `fire_id` before creating another run.
- Invalid schedules or revoked permissions produce terminal, user-visible skip reasons.
- Scheduler outage recovery applies the stored misfire policy; it never launches an unbounded backlog.
- A failed audit write prevents activation or dispatch for security-relevant transitions.

## Observability and quality gates

Metrics:

- proposal acceptance/rejection rate and edit distance;
- activation latency;
- due-to-dispatch lag;
- claim contention and lease expiry;
- duplicate fire/run attempts;
- pre-fire denial reasons;
- skipped/misfired occurrences;
- per-routine cost, tool calls, approvals, and outcomes; and
- scheduler heartbeat and oldest-due-fire age.

Tracing must link `proposal_id → routine_id/revision → fire_id → run_id → operation_id`. Logs contain IDs and reason codes, not prompt bodies or secrets.

Release gates:

- zero duplicate successful dispatches in fault-injection tests;
- 100% of fires carry a current policy snapshot and authority envelope;
- no production startup with the in-memory routine store;
- deterministic DST and missed-fire fixtures; and
- tenant-isolation, revocation, deletion, and audit-export tests pass.

## Rollout and backout

1. Ship PostgreSQL tables and adapter with scheduler disabled.
2. Dual-write or migration-import existing durable routine records where applicable.
3. Enable proposal review for internal tenants without activation.
4. Enable scheduler shadow mode and compare due calculations.
5. Enable real fires for allowlisted tenants with low concurrency.
6. Expand recurrence and webhook support after reliability gates hold.

Backout pauses claiming globally, leaves definitions and audit history intact, cancels undispatched claims, and allows active runs to finish or be explicitly cancelled. Schema rollback is not required for feature backout.

## Implementation slices

1. PostgreSQL routine/revision/fire store and migrations
2. Proposal review and compile endpoint
3. Facade contracts and routine management UI
4. Transactional claim, lease, heartbeat, and outbox dispatch
5. Worker scheduler composition and health checks
6. Pre-fire grant issuance and AI backend verification
7. Misfire, overlap, retry, revoke, retention, and audit completion
8. Evaluation dashboard and tenant rollout controls

## Test plan

- Unit: schedule parsing, timezone/DST, compiler validation, dedupe keys, policy narrowing
- Store contract: equivalent behavior across in-memory and PostgreSQL adapters
- Integration: accepted proposal through first completed fire
- Concurrency: multiple schedulers claim the same due batch
- Fault injection: crash before/after claim, run dispatch, heartbeat, and completion
- Security: cross-tenant IDs, stale role, revoked connector, forged webhook, replayed nonce
- Governance: approval does not widen later fires; A5 reconciliation after timeout
- Retention: user deletion, tenant deletion, legal hold, audit export
- Load: large pending-fire set with bounded indexed scans

## Definition of done

- An authorized user can review, edit, and activate an evidence-backed proposal.
- Routine definitions and fire history survive service and database failover.
- The production worker starts and monitors the scheduler.
- Every fire is deduplicated, policy-checked, traceable, cancellable, and visible.
- A restart or lost network response cannot create duplicate successful runs.
- Revocation is enforced before the next tool call and before every new fire.
- Product, security, SRE, and compliance owners sign off on the release gates.

## Guardrails

- Suggestion is not authorization.
- Past approval is not future authority.
- A routine may narrow but never widen the activating actor's permissions.
- No arbitrary code, shell command, plaintext secret, or hidden connector installation.
- No unbounded catch-up, recursion, overlap, cost, or retention.
- Disabled audit, policy, credential, or durable-store dependencies fail closed.

## Open decisions

1. Whether material schedule-only edits require the original owner or any tenant automation administrator.
2. Maximum catch-up occurrences and default daily cost budget by tenant tier.
3. Whether ownership transfers are supported or require clone-and-reapprove.
4. Which recurrence-rule subset is committed for the first public release.
5. Whether webhook routines require a separate tenant administrator enablement.
