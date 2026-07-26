# PRD-AR-F6 — Capability concurrency and safe batching

**Status:** proposed\
**Priority:** P2\
**Owners:** AI Runtime, Connector Platform, Reliability\
**Depends on:** [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md),
[A5 commit coordinator](../../generative-surfaces-v2-1/prds/PRD-A5-commit-coordinator.md),
[D1 MCP convergence](../../generative-surfaces-v2-1/prds/PRD-D1-mcp-convergence.md), and
[D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md)

## Goal

Reduce wall time for independent operations without racing writes, violating connector
limits, losing ordered events, or weakening per-call authorization and accountability.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/execution/factory.py`.
2. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py`.
3. `services/ai-backend/src/agent_runtime/capabilities/actions/`.
4. `services/ai-backend/src/agent_runtime/persistence/records/tool_budgets.py`.
5. `services/ai-backend/src/runtime_worker/stream_tools.py`.
6. `services/ai-backend/src/runtime_worker/stream_events.py`.
7. A3, A5, D1, and D2.

Existing concurrent registry bootstrap is not the execution contract. This PRD covers
model-emitted and program-emitted operation batches after descriptor resolution.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem and current strengths

Three independent connector reads or file inspections should not necessarily wait for
one another. Sequential latency is approximately the sum of call latencies. However,
tool schemas rarely state enough concurrency semantics to safely parallelize. Two
writes to the same record, a read-after-write, or a server with non-thread-safe session
state can produce incorrect results.

The Operation Gateway, staged effects, commit coordinator, typed events, cancellation,
and operation trees provide the correct execution boundary. Concurrency must be an
explicit descriptor and scheduler policy at that boundary, not a model assumption.

## Objectives

1. Add side-effect, idempotency, concurrency, resource-key, and rate-limit metadata.
2. Convert an ordered operation batch into deterministic serial/parallel segments.
3. Bound concurrency globally and by profile/user/connector/capability.
4. Recheck each operation independently through A3/D1/D2.
5. Preserve stable result/event ordering while exposing actual completion order.
6. Cancel queued/in-flight work safely and report partial outcomes honestly.
7. Demonstrate lower p95 wall time without write races or rate-limit regressions.

## Non-goals

- Making all MCP servers or reads parallel by default.
- Transactionally committing unrelated external systems.
- Combining approvals for mutations; A4/A5 own effect decisions.
- Defining programmatic dataflow; F7 consumes this executor.
- Scheduling subagent conversations; F9 owns delegation policy.
- Retrying uncertain external effects.

## Interfaces consumed

- A3 `OperationRequest`, descriptor, classification, and gateway disposition.
- A5 effect stage/decision/claim/commit records.
- D1/D2 adapters, operation tree, budgets, cancellation, and event sink.
- Connector/provider rate-limit and session-capability metadata.

## Interfaces exposed

```text
ConcurrencyPolicy
  mode: serial | parallel_safe | same_subject_serial
  side_effect: none | read | reversible_write | irreversible_write | unknown
  idempotency: none | keyed | natural
  resource_key_template?
  max_parallelism?
  rate_limit_scope: capability | connector | user | installation | global
  ordering_requirement: none | input_order | completion_order
  policy_source: product_catalog | trusted_provider | conservative_default

OperationBatch
  batch_id, parent_operation_id
  operations[]
  deadline
  max_parallelism
  failure_policy: fail_fast | collect_all | stop_new

BatchSegment
  segment_index
  mode: serial | parallel
  operation_ids[]
  reason

BatchResult
  batch_id
  ordered_results[]
  status: completed | partial | failed | cancelled
  started_at, completed_at
```

Ports:

- `ConcurrencyPolicyResolver.resolve(descriptor, context)`.
- `BatchPlanner.plan(batch, policies)`.
- `BatchExecutor.execute(plan, context)`.
- `RateLimitCoordinator.acquire(scope_key)`.

Events:

- `operation.batch.planned.v1`
- `operation.batch.started.v1`
- normal child `operation.*` events
- `operation.batch.completed.v1`

## Detailed design

### 1. Conservative metadata resolution

Precedence:

1. checked-in product capability catalog;
2. user-approved connector override;
3. trusted provider metadata that can only tighten policy;
4. default `serial`, `unknown`, `idempotency=none`.

`readOnly` alone does not imply thread safety. MCP capabilities require explicit server
or product opt-in before concurrent execution.

### 2. Resource keys and dependencies

Each request may resolve a safe resource key such as connector/account/object ID.
Arguments used to derive the key are canonicalized before scheduling and never logged.

Dependencies are inferred from:

- explicit batch edges;
- same-subject serial policy and equal resource key;
- reads depending on prior write/effect result;
- stage/approval barriers;
- provider session serialization.

Unknown dependency means serial.

### 3. Segment planning

Walk input operations in order. Accumulate a parallel segment only while all operations
are eligible, dependency-free, within the same authorization epoch, and below effective
limits. Insert serial/barrier segments for writes, unknowns, approvals, or dependencies.
The plan and reason codes are persisted before execution.

### 4. Execution

Each child independently:

1. re-enters the Operation Gateway;
2. acquires rate-limit/concurrency permits;
3. runs with a child deadline/cancellation token;
4. emits normal operation, usage, citation, and result records;
5. releases permits in `finally`.

The batch collector returns results in input order with completion timestamps. One
child's exception cannot erase successful sibling records.

### 5. Writes

Effect proposals may be prepared concurrently only if preparation is read-only and
declared safe. Actual mutations remain A5-controlled and serial by resource unless a
designated executor supplies a stronger transactional contract. `parallel_safe` cannot
downgrade exact approval or claim-before-effect.

### 6. Cancellation and deadlines

Cancellation stops admission of queued work, sends cancellation to cancellable reads,
and waits a bounded drain period. Non-cancellable or possibly applied operations remain
`in_flight`/`indeterminate` for reconciliation. The batch is `partial`, not rolled back
by fiction.

## Security, local-profile boundaries, privacy, and audit

- Effective limits and policies derive from verified local profile/runtime context.
- Permits are keyed with profile/user/connector scope to prevent noisy-neighbor bypass.
- Raw resource keys are stored as keyed digests unless user-visible identity is needed
  and authorized.
- Every child retains its own authorization, approval, budget, redaction, citation, and
  audit records.
- Parallel execution cannot reuse one user's connector client/credential in another
  user's context.
- Policy changes and user overrides are audited.

## Performance and complexity budgets

For `k` operations, planning is `O(k log k)` at worst and `O(k)` for ordered grouping.
Execution work remains `O(k)`.

- Default batch cap: 16 operations.
- Default worker cap: 8 globally, further limited per connector/installation.
- Planner p95 below 5 ms for 100 operations.
- Ideal balanced read latency target approaches `ceil(k/p) × L`; production target is
  at least 30% p95 improvement on three independent 500 ms reads.
- Queue and permit acquisition times are individually measured.
- No unbounded task creation or result accumulation.

## Failure, idempotency, and recovery

- Batch plan identity binds ordered request digests and policy revision.
- Same batch key/same digest replays terminal results; changed digest conflicts.
- Child idempotency remains the designated adapter/executor contract.
- Worker restart reconstructs batch status from immutable child operations and resumes
  only never-started safe reads.
- Started writes are never blindly retried.
- Rate-limit response may delay/retry idempotent reads under bounded policy; otherwise
  it returns structured failure.
- Scheduler/process failure cannot mark missing children successful.

## Observability and quality gates

Measure:

- eligible versus serialized operations by reason;
- planned/effective concurrency;
- queue, permit, execution, and tail latency;
- per-connector rate-limit and saturation;
- cancellations, partial batches, indeterminate children;
- write-race/precondition conflicts;
- operation/event ordering violations;
- total work/cost and end-to-end task success.

Safety gate: zero improper parallel writes and zero authorization/profile-context reuse.

## Rollout and backout

1. Resolve policies and produce shadow batch plans; execute serially.
2. Enable parallel synthetic and built-in pure reads.
3. Enable curated first-party connector reads.
4. Add explicitly opted-in MCP servers.
5. Allow F7 read-only dataflow to consume the executor.
6. Evaluate any staged-preparation concurrency separately.

Global, connector, and capability flags force serial execution immediately. Backout
does not alter operation results or effect stages.

## Implementation slices

1. Metadata contracts, conservative resolver, and catalog fixtures.
2. Deterministic batch planner and shadow events.
3. Bounded executor with permits, ordering, deadlines, and cancellation.
4. Built-in read adapter enablement.
5. Connector/MCP opt-in and rate-limit coordination.
6. Recovery reducer, dashboards, and conformance gates.

## Test plan

- Independent equal-latency reads execute concurrently and return input order.
- Same-resource operations serialize.
- Read-after-write dependency inserts a barrier.
- Unknown or missing metadata defaults serial.
- MCP server without explicit opt-in remains serial.
- One child failure preserves successful siblings and produces `partial`.
- Cancellation stops queued work and reports non-cancellable state honestly.
- Worker crash resumes never-started reads but never retries started writes.
- Rate-limit permits isolate profile/user/connector scopes.
- Cross-profile client/session reuse canary fails.
- Event sequence numbers remain monotonic under out-of-order completion.

## Definition of done

- Every model/program batch has a persisted deterministic plan.
- Only explicitly eligible independent operations run concurrently.
- Child calls retain full gateway, approval, usage, citation, and audit behavior.
- Recovery and cancellation never invent rollback or success.
- p95 read-batch latency improves without rate-limit or correctness regression.
- Serial kill switch, dashboards, runbook, and F1 coverage are complete.

## Guardrails and open decisions

Guardrails:

- Read-only is not synonymous with parallel-safe.
- Unknown metadata always serializes.
- Writes require resource-aware ordering and remain A5-owned.
- Do not optimize wall time by increasing unbounded provider load.

Open decisions:

1. Which connector metadata is trusted to declare parallel safety?
2. For a future hosted adapter, when do multiple workers justify a shared permit service?
3. Which failure policy should model-emitted batches default to?
4. How should provider-specific batch APIs integrate without changing child records?
