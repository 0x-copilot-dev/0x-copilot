# PRD-AR-F7 — Governed dataflow and programmatic tool calling

**Status:** proposed\
**Priority:** P2\
**Owners:** AI Runtime, Security, Connector Platform\
**Depends on:** [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md),
[A4 effect stager](../../generative-surfaces-v2-1/prds/PRD-A4-effect-stager.md),
[A5 commit coordinator](../../generative-surfaces-v2-1/prds/PRD-A5-commit-coordinator.md),
[D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md), and
[F6 safe batching](PRD-AR-F6-capability-concurrency-safe-batching.md)

## Goal

Execute bounded mechanical workflows—map, filter, branch, aggregate, and invoke
authorized read capabilities—without a model round trip for every item. Every inner
call must remain independently authorized, metered, attributable, and evidence-bearing.

## Implementer brief

Read:

1. `services/ai-backend/src/runtime_worker/capability_tool_wiring.py`.
2. `services/ai-backend/src/agent_runtime/capabilities/interpreter/`.
3. `services/ai-backend/src/agent_runtime/capabilities/sandbox/`.
4. `services/ai-backend/src/agent_runtime/execution/factory.py`.
5. `services/ai-backend/src/agent_runtime/capabilities/actions/`.
6. D2 §Code mode, A3–A5, and F6.

The current code-mode tool is deliberately pure compute unless explicitly supplied
external functions. Retain that safe default. This PRD defines the only allowed bridge
from a program/dataflow to external capabilities; the remote sandbox remains a separate
isolated execution adapter under D3.

## Problem and current strengths

Workflows such as “list 100 records, retain those matching a rule, fetch details for
the survivors, and compute a summary” are program-shaped. A model-mediated loop needs
approximately one inference per iteration and repeatedly carries intermediate data in
context.

The runtime already has a constrained interpreter, capability gateway, budgets,
approvals, operation lineage, artifacts, and staged effects. These permit a safer
design than arbitrary host Python: a typed dataflow plan over an explicitly supplied
capability set, with every call routed through the normal gateway.

## Objectives

1. Define a versioned, bounded dataflow language with no ambient authority.
2. Materialize callable functions only from the current authorized capability set.
3. Preserve per-inner-call validation, policy, budget, citation, redaction, and audit.
4. Keep intermediate bulk data outside primary model context.
5. Return a structured aggregate plus retrievable evidence artifact.
6. Support safe parallel reads through F6.
7. Add mutation planning only as an exact A4 proposal manifest, never direct execution.

## Non-goals

- Arbitrary Python, imports, sockets, filesystem, environment, subprocess, or shell.
- Passing connector credentials into the program.
- Direct MCP/browser/workspace/network access.
- Recursive dataflow, subagent delegation, or model invocation from a plan.
- Combining many side effects behind one undifferentiated approval.
- Guaranteeing fewer external calls; the goal is fewer model turns and bounded context.

## Interfaces consumed

- Authorized `CapabilityDescriptor` and A3 operation adapters.
- D2 constrained interpreter and operation tree.
- F6 batch planner/executor and concurrency metadata.
- A2/B1 artifact/result refs.
- A4/A5 proposal, decision, and effect execution contracts.
- Existing budget, UsageMeter, cancellation, and event sinks.

## Interfaces exposed

### Plan contract

```text
DataflowPlan
  plan_id, language_version
  input_bindings[]
  capability_bindings[]
  nodes[]
  output_schema
  limits
  plan_digest

DataflowNode
  node_id
  op: map | filter | select | sort | limit | reduce | group |
      branch | invoke | batch_invoke | emit
  inputs[]
  expression?
  capability_binding?
  error_policy

DataflowLimits
  max_nodes
  max_input_items
  max_iterations
  max_inner_calls
  max_parallelism
  max_result_bytes
  max_cpu_ms
  max_wall_ms

DataflowResult
  plan_id, operation_id
  status
  output
  output_artifact_ref?
  evidence_manifest_ref
  child_operation_ids[]
  counts
  truncated
```

### Model-facing built-in

```text
run_dataflow(plan, inputs?)
```

The model receives only bindings explicitly present in the run. Capability bindings use
opaque refs from the current authorized catalog/descriptor set.

### Mutation extension contract

```text
EffectBatchManifest
  plan_digest
  item_count
  effect_items[]
  canonical_arguments_ref
  target/precondition_refs[]
  compensation_metadata?
  manifest_digest
```

### Events

- `dataflow.plan.validated.v1`
- `dataflow.execution.started.v1`
- normal child `operation.*` events
- `dataflow.effect_manifest.proposed.v1`
- `dataflow.execution.completed.v1`

## Detailed design

### 1. Language and validation

Use a closed JSON AST, not source text. Expressions support literals, field access,
bounded comparisons, boolean logic, arithmetic, string/date primitives, and pure
collection transforms. No reflection, dynamic property traversal outside input schema,
recursion, user-defined functions, or runtime imports.

Validation performs:

- schema/type checking;
- acyclic graph verification;
- static upper-bound analysis for nodes/iterations/calls;
- capability binding resolution;
- output-size estimate;
- forbidden operation and recursive-binding rejection.

Plans that cannot prove a bound are rejected.

### 2. Capability binding

The runtime builds bindings after current authorization filtering. Each includes an
opaque ref, input/output schema, effect class, and limits. The interpreter receives a
stub that submits a canonical `OperationRequest`; it never receives a transport client,
URL, token, session, or credential.

Each invocation re-resolves current policy. Revocation during execution blocks later
calls and yields a partial structured result.

### 3. Execution and intermediate data

The executor stores large inputs/intermediates in a run-scoped data store or A2 refs.
Only bounded values cross the interpreter boundary. Inner tool results are normalized
to typed values plus evidence/source refs.

Each child call receives deterministic identity from `(plan_id, node_id, item_index,
attempt)`. F6 may parallelize only explicitly safe independent reads. Program output is
validated against `output_schema`.

### 4. Result and evidence

Inline result defaults below 32 KiB. Larger output becomes an immutable artifact. An
evidence manifest maps aggregate rows/claims to child operation/result/source refs and
records excluded/error counts. The primary model receives a compact summary, counts,
and refs—not every raw record.

### 5. Errors

Closed node error policies:

- `fail_plan`;
- `skip_item`;
- `collect_error`;
- `stop_new`.

Policies cannot retry writes. Read retries remain the underlying capability's bounded
policy and are visible as child attempts.

### 6. Mutation phase

Mutation-capable bindings are disabled initially. Later, a dataflow may compute an
`EffectBatchManifest`; it cannot invoke effects. A4 stages exact per-item canonical
arguments, targets, preconditions, and plan digest. Approval UI must permit reviewing
the exact set; execution revalidates each item through A5. Partial/indeterminate
outcomes remain itemized.

## Security, tenancy, privacy, and audit

- Runtime context supplies identity and capability bindings; plan fields cannot.
- Interpreter has no ambient network/filesystem/process/environment access.
- Input/result refs are run- and tenant-scoped and reauthorized on every read.
- Secret-like values are rejected from inline input/output and never exposed as
  capability binding data.
- Every inner call produces normal operation, usage, citation, and audit records.
- Plan, digest, limits, policy revision, child lineage, and evidence manifest are
  retained under E1 lifecycle.
- A skill or model plan cannot widen tools beyond current runtime authority.

## Performance and complexity budgets

For `k` inner calls:

- model round trips target `O(1)` for plan authoring/result review instead of `O(k)`;
- external operations remain `O(k)`;
- pure transforms are `O(n)` unless explicit sort/group (`O(n log n)`);
- default limits: 100 nodes, 1,000 input items, 50 inner calls, 8-way read concurrency,
  32 KiB inline output, 300 seconds wall time;
- validation p95 below 10 ms for maximum-size plans;
- interpreter memory/CPU and artifact bytes have hard quotas.

All defaults are configuration policy, versioned and reducible by org/run.

## Failure, idempotency, and recovery

- Plan validation is pure and deterministic by language/policy/capability revisions.
- Execution idempotency binds plan digest, input digests, and capability revisions.
- Each child has a deterministic idempotency key; same digest replays safe terminal
  reads.
- Snapshot progress before admitting the next batch of calls.
- Worker crash resumes only from the last durable snapshot and never blindly repeats an
  uncertain operation.
- Revoked/stale capability stops dependent nodes.
- Oversize output is offloaded or fails closed; never truncated without `truncated`.
- Mutation manifestations are immutable once staged.

## Observability and quality gates

Measure:

- plans accepted/rejected by reason;
- model turns avoided, child calls, items processed, and parallelism;
- validation/execution/queue/provider latency;
- inline/intermediate/artifact bytes;
- partial/error/skipped counts;
- evidence coverage and aggregate correctness;
- policy/authorization blocks and attempted forbidden operations;
- cost/task success against ordinary agent loops.

F1 cases must include exact bulk filtering, pagination, revocation mid-plan, malformed
schema, oversized input/output, child failure, and effect-manifest safety.

## Rollout and backout

1. Pure local transforms over inline synthetic data.
2. Read-only first-party deterministic capabilities.
3. Read-only connector/web/file capabilities through A3 and F6.
4. Larger inputs/outputs via artifact refs and evidence manifests.
5. Shadow mutation-manifest generation.
6. Reviewed batch proposals for a small reversible effect class.

The capability can be disabled globally, by org, or by capability. Backout leaves
ordinary direct tools and pure code mode available; staged effects remain A4/A5-owned.

## Implementation slices

1. AST, limits, validator, canonical digest, and golden fixtures.
2. Pure executor with snapshot/quotas.
3. Authorized capability-binding factory and child operation dispatcher.
4. F6 batching, cancellation, and recovery.
5. Evidence manifest and artifact offload.
6. Model-facing tool, prompts, telemetry, and F1 suite.
7. Optional mutation-manifest phase under a separate launch flag.

## Test plan

- Static bounds reject recursion, dynamic calls, imports, and unbounded loops.
- Forged capability refs and revoked capability bindings fail.
- Interpreter cannot reach filesystem/network/environment/subprocess.
- Every inner call passes A3 and has usage/audit/evidence lineage.
- Bulk oracle output is exact and retrievable.
- Intermediate records do not enter model context.
- Parallel reads preserve deterministic output and limits.
- Worker crash resumes safe reads without duplicate uncertain calls.
- Output limit uses artifact/offload and declares truncation.
- Mutation plan creates a stage with zero effect dispatch.
- Cross-tenant input/evidence refs fail.

## Definition of done

- Read-only dataflows reduce model turns on fixed bulk tasks without reducing accuracy.
- No ambient authority or transport client is accessible to the program.
- Every inner call is independently governed and observable.
- Outputs are typed, bounded, and evidence-addressable.
- Recovery is snapshot-based and honest about partial/uncertain state.
- Mutation support, if enabled, produces only exact reviewable A4 manifests.

## Guardrails and open decisions

Guardrails:

- Do not expose arbitrary source execution as a shortcut.
- Do not batch approvals into an opaque outer call.
- Do not return free-form stdout as the sole result contract.
- Do not enable mutation bindings before read-only quality and safety gates pass.

Open decisions:

1. Implement the AST in Monty, a dedicated interpreter, or a pure domain executor?
2. Which pure expression primitives are required for the first task corpus?
3. What checkpoint granularity balances recovery writes and duplicate-call risk?
4. Which reversible effect class, if any, should be the first manifest pilot?
