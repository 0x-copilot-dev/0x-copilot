# PRD-AR-F9 — Parallel delegation quality controller

**Status:** proposed\
**Priority:** P2\
**Owners:** AI Runtime, Applied AI, Product\
**Depends on:** [D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md),
[E1 accountability](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md),
[F1 evaluation](PRD-AR-F1-harness-observability-evaluation-promotion.md), and
[F6 safe batching](PRD-AR-F6-capability-concurrency-safe-batching.md)

## Goal

Use subagents only when work is sufficiently independent and valuable, provide each
child the minimum complete context and authority, bound aggregate cost/concurrency, and
verify child evidence before the supervisor relies on it.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/delegation/subagents/contracts.py`.
2. `services/ai-backend/src/agent_runtime/delegation/subagents/authority.py`.
3. `services/ai-backend/src/agent_runtime/delegation/subagents/runner.py`.
4. `services/ai-backend/src/agent_runtime/delegation/subagents/atlas_task_tool.py`.
5. `services/ai-backend/src/agent_runtime/delegation/subagents/handoff.py`.
6. `services/ai-backend/src/agent_runtime/context/memory/subagent_trace.py`.
7. `services/ai-backend/src/runtime_worker/stream_subagents.py`.
8. D2 §Subagent operation tree.

D2 remains authoritative for operation identity, capability intersection, artifacts,
stages, cancellation lineage, and usage attribution. This PRD adds the admission,
context-packet, scheduling-budget, result, and supervisor-verification contract for
in-run delegation.

## Problem and current strengths

The runtime compiles Deep Agents subagents and adds task-call correlation, typed
lifecycle events, artifacts, citations, approvals, trace projection, and durable
records. Authority can be constrained and child operations are attributable.

Parallel children can still make a task worse when:

- the work is dependent or too small to amortize another model session;
- the parent sends vague/incomplete context;
- every child receives irrelevant or sensitive transcript material;
- fan-out exceeds org/provider budget;
- summaries conflict or cite unsupported claims;
- child cancellation/partial completion is hidden by the parent.

Delegation quality is therefore a policy and contract problem, not merely the presence
of a task tool.

## Objectives

1. Admit delegation only for explicit, sufficiently independent work units.
2. Require a bounded structured context/evidence packet.
3. Intersect child authority with parent, definition, org policy, and task need.
4. Enforce run/org/user concurrency, token, cost, call, and wall-time budgets.
5. Return a typed result with claim-to-evidence links and uncertainty.
6. Verify required evidence, contradictions, and output schema before synthesis.
7. Make child progress, partial results, artifacts, and cancellation inspectable.

## Non-goals

- Persistent work across runs/restarts as a multi-day job board.
- Giving children the full parent transcript by default.
- Allowing children to widen connector, workspace, approval, or tenant scope.
- Automatically resolving conflicting child conclusions with another unbounded model.
- Delegating effectful tasks merely to increase throughput.
- Replacing D2 operation trees or F6 capability-call scheduling.

## Interfaces consumed

- D2 `SubagentDefinition`, authority intersection, task/call identity, operation tree,
  artifacts, stages, and usage attribution.
- Parent plan/evidence refs and F5-style bounded context representations.
- Provider/org/run budgets and cancellation.
- F1 evaluator and existing stream/activity events.

## Interfaces exposed

```text
DelegationRequest
  delegation_id
  parent_operation_id
  subagent_slug
  task
  expected_output_schema
  context_packet
  authority_request
  budget_request
  deadline
  dependency_refs[]

DelegationContextPacket
  goal
  relevant_facts[]
  evidence_refs[]
  constraints[]
  exclusions[]
  source_scope
  output_requirements
  packet_digest

DelegationBudget
  max_model_turns
  max_tool_calls
  max_input/output_tokens
  max_cost
  max_wall_ms

DelegationResult
  delegation_id, child_task_id
  status: completed | partial | blocked | failed | cancelled
  structured_output
  claims[]
  evidence_refs[]
  artifact_ids[]
  stage_ids[]
  uncertainty[]
  usage_summary

DelegationVerification
  schema_valid
  evidence_coverage
  contradictions[]
  policy_findings[]
  disposition: accept | accept_with_caveats | retry | reject
```

Ports:

- `DelegationAdmissionController.assess(request, parent_state)`.
- `DelegationPacketBuilder.build(parent_state, request)`.
- `DelegationScheduler.submit_batch(requests)`.
- `DelegationResultVerifier.verify(result, request)`.

Events:

- `subagent.delegation.admitted.v1`
- `subagent.delegation.rejected.v1`
- normal D2 child lifecycle/operation events
- `subagent.result.verified.v1`

## Detailed design

### 1. Admission

Admission checks:

- task has a concrete output and success condition;
- expected child work exceeds configured minimum;
- dependency graph permits concurrency;
- task does not require the same unresolved approval/effect sequence;
- a suitable enabled subagent definition exists;
- effective authority and budgets are non-empty;
- expected benefit is not dominated by setup/cost.

A deterministic rule handles obvious cases. An optional classifier may recommend
delegation but cannot override policy or increase scope/budget.

### 2. Context packet

The parent supplies task-specific facts, source/evidence refs, explicit constraints,
allowed capabilities, and output schema. The packet excludes unrelated transcript,
credentials, hidden reasoning, and physical host paths.

Every ref is reauthorized in child context. Inline text is size bounded and labelled by
trust. The packet digest is persisted and visible in trace metadata.

### 3. Authority and budget

Effective child authority is:

`parent grant ∩ subagent definition ∩ org/user policy ∩ task request`.

Effective budget is the minimum of parent remaining budget, definition limits, org
limits, and requested limits. Budget is reserved before scheduling and charged from
actual usage; unused reservation returns.

### 4. Parallel scheduling

Batch requests declare dependencies. Only independent admitted children run together.
Default maximum is three active children per parent, further bounded by org/provider
limits. Scheduling is fair across tenants and cancellation-aware.

Subagents do not share mutable conversation state. Shared workspace/effect targets
remain serialized through their designated operation/effect contracts.

### 5. Result contract

Children return typed output and concise claims, each referencing evidence where the
schema requires it. Published artifacts/stages remain canonical D2/B1/A4 records;
failure to serialize the final envelope does not delete them.

Only the bounded verified result enters parent context. Full trace remains inspectable
through authorized refs.

### 6. Verification and synthesis

Deterministic checks run first:

- output schema and size;
- evidence refs exist and are authorized;
- required claim coverage;
- forbidden capability/effect findings;
- budget/deadline status.

For multi-child comparisons, normalize claims/entities and detect contradictory values
or conclusions. The supervisor receives contradictions and caveats explicitly. An
optional bounded verifier model may assess support but cannot erase deterministic
findings.

### 7. Retry

Retry requires a reason code and revised request/packet. It consumes reserved budget
and receives a new child task ID linked to the prior attempt. No automatic retry after
an uncertain effect or policy violation.

## Security, tenancy, privacy, and audit

- Parent identity does not become transferable credentials; child context is
  server-derived.
- Authority subset is asserted at construction and before every child operation.
- Context/evidence refs are reauthorized; the parent cannot smuggle another tenant's
  ref.
- Packets, summaries, and claim text are untrusted and size limited.
- Cost/concurrency quotas are tenant-aware and cannot be changed by a model.
- Delegation admission, authority, packet digest, budget, cancellation, result, and
  verification are audited.
- Retention/deletion covers child conversations, operations, artifacts, summaries,
  packet refs, and verification records.

## Performance and complexity budgets

For `n` independent children and concurrency `p`, ideal wall time approaches the
slowest waves rather than the sum; model/token cost remains approximately additive.

- Default active children per parent: 3; hard configurable cap: 8.
- Inline packet target below 8,000 tokens; result below 4,000 tokens.
- Admission/packet deterministic processing p95 below 10 ms.
- Dependency planning `O(n + e)` with at most 32 children per batch.
- Budget reservation happens before any child model call.
- First release supports one delegation depth unless an explicit nested-orchestration
  policy is approved.

## Failure, idempotency, and recovery

- Delegation submission idempotency binds parent operation, request, packet, authority,
  and budget digests.
- Same key/same digest returns the existing child task; changed digest conflicts.
- Scheduler crash reconstructs queued/completed state from D2 records.
- In-flight child model execution after process loss is not assumed resumable; mark
  interrupted and retry only under normal safe run policy.
- Cancellation cascades to active child operations but cannot undo applied effects.
- Partial child results remain available and are labelled partial.
- Budget reservation is reconciled exactly once against usage records.

## Observability and quality gates

Measure:

- admission/rejection by reason and task family;
- child concurrency, queue time, wall-time saved estimate;
- packet/result tokens and duplicated context;
- aggregate cost and parent/child tool calls;
- schema/evidence verification failures;
- contradictions and supervisor caveat handling;
- retries, cancellations, partial/interrupted tasks;
- task success against single-agent control.

F1 includes independent research, dependent editing, insufficient packet, conflicting
children, unauthorized ref, overspend, and cancellation scenarios.

## Rollout and backout

1. Validate/record requests and packets while existing delegation executes.
2. Enforce authority/budget and typed results for one internal subagent.
3. Enable deterministic verifier and visible caveats.
4. Enable parallel scheduling for independent read-only tasks.
5. Add task-family admission and optional classifier recommendation.
6. Expand definitions after F1 evidence.

Backout forces one child at a time or disables model-facing delegation while retaining
D2 records/artifacts and direct supervisor execution.

## Implementation slices

1. Request/packet/budget/result/verification contracts.
2. Admission and authority/budget reservation.
3. Packet builder with evidence ref reauthorization.
4. Scheduler and dependency/concurrency controls.
5. Typed result adapter and deterministic verifier.
6. Contradiction projection, UI events, F1 suite, and dashboards.

## Test plan

- Tiny/dependent task is rejected with a useful reason.
- Three independent tasks run within cap and return stable result order.
- Child receives only packet content, not full parent transcript.
- Authority/tool/workspace/tenant widening attempts fail.
- Forged/expired evidence refs fail in child context.
- Budget reservation prevents oversubscription and reconciles once.
- Contradictory child claims are surfaced to the supervisor.
- Malformed final envelope preserves already-published artifacts/stages.
- Cancellation and process-loss states are honest.
- Same-key replay does not create duplicate child tasks.
- Single-agent control wins for small task and policy learns no automatic override.

## Definition of done

- Every child has a complete bounded packet, authority subset, budget, deadline, and
  expected output schema.
- Parallelism applies only to admitted independent work.
- Results are evidence-linked, verified, and caveated before parent synthesis.
- Aggregate usage/cost and child operation trees are inspectable.
- F1 demonstrates quality/wall-time value on intended tasks without policy regression.
- Kill switches, dashboards, backout, and runbook are shipped.

## Guardrails and open decisions

Guardrails:

- More agents is not itself a quality objective.
- Never copy the full transcript by default.
- Never let a child broaden authority or self-allocate budget.
- Never hide partial/cancelled/contradictory results in synthesis.

Open decisions:

1. Which subagent definitions ship enabled by default?
2. Should nested delegation remain disabled for the first release?
3. What minimum expected work justifies child setup?
4. Which claim types require deterministic evidence coverage?
