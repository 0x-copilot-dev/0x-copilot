# Agent Runtime Quality implementation backlog

This file records confirmed implementation defects and architectural follow-up
discovered while delivering the F-series PRDs. Items are retained after a fix
lands so the design history remains auditable.

## Resolved

### ARQ-001 — F1 canonical digests rejected valid typed contracts

- **Found in:** F1 focused evaluation tests.
- **Impact:** `TrajectoryManifest` and `EvaluationResult` digests received
  tuples and Pydantic contracts, although the cross-language canonical JSON
  primitive accepts JSON lists/dictionaries only. Valid evaluation records
  therefore failed before persistence.
- **Architectural fix:** Normalize the complete digest material through
  Pydantic's JSON serializer at the one F1 digest boundary, then hash the
  resulting JSON-safe value. This preserves the shared cross-language
  canonical-hash contract and avoids per-model conversion workarounds.
- **Status:** resolved in the F1 foundation slice; regression-tested.

### ARQ-002 — The base prompt can terminate a tool run at a progress checkpoint

- **Found in:** F2–F6 implementation audit; verified in
  `agent_runtime/prompts/runtime.py` and
  `agent_runtime/execution/deep_agent_builder.py`.
- **Impact:** the base instruction asks for a plain-text checkpoint before the
  next tool call, while the Deep Agents loop correctly treats a tool-call-free
  message as final. The resulting contradictory prompt can terminate a run and
  cause redundant supervisor delegation.
- **Required architectural fix:** replace ad-hoc overlapping prompt strings
  with F2's typed prompt-fragment assembly and a single progress-checkpoint
  rule. Until F2 lands, remove the contradictory base fragment and retain the
  loop-safe same-message rule as the sole source of truth.
- **Status:** resolved for the active harness: the contradictory base fragment
  was removed and a regression test pins the loop-safe suffix as the sole
  checkpoint instruction. F2 will migrate that surviving rule into a typed
  prompt fragment rather than reintroduce a second source.

### ARQ-003 — Tool budgets do not govern every model-visible tool

- **Found in:** F2–F6 implementation audit; verified in
  `runtime_worker/dependencies.py`,
  `agent_runtime/capabilities/tool_budget_guard.py`, and
  `agent_runtime/execution/factory.py`.
- **Impact:** the registry decorator applies budgets only to registry-provided
  tools. MCP/skill/prior-result/ask and other factory-injected tools bypass the
  hard per-run budget, so the current safety claim is false.
- **Required architectural fix:** apply one common runtime tool-controller
  wrapper after the complete model-visible tool surface is assembled. F4 will
  own this wrapper and make the same admission record drive budgets, duplicate
  detection, and progress.
- **Status:** resolved: the complete model-visible surface is now wrapped
  before policy decoration, so policy-blocked tools consume no budget while
  every policy-admitted `BaseTool` is budget-governed. Regression coverage
  proves the wrapper is idempotent and policy decoration remains visible.

## Open

### ARQ-004 — Desktop tool-result offload happens after model context admission

- **Found in:** F2–F6 implementation audit; verified in
  `runtime_worker/tool_result_offload.py`.
- **Impact:** offload reduces persisted event/UI payload size but occurs after
  the raw tool result has already reached the current agent graph. A single
  oversized result can therefore still exhaust the model context.
- **Required architectural fix:** move bounded-result representation to the
  common tool-result boundary before the result becomes a model `ToolMessage`.
  F5's `ContextBudgeter` and evidence hydration contract will own this, with
  the current file-backed content-addressed store retained as the backing ref.
- **Status:** partially addressed. `ToolResultAdmissionAdapter` now owns
  deterministic serialization, bounded model content, content-addressed
  offload, and the worker's persisted-event projection. It is not yet called
  by the Deep Agents/LangChain tool wrapper before its result becomes a
  `ToolMessage`, so the production current-turn context remains unbounded.
  This stays open and blocks F5 context enforcement.

### ARQ-005 — Generic worker retry can replay a completed portion of a run

- **Found in:** F7–F12 implementation audit; verified in
  `runtime_worker/loop.py` and `execution/runtime.py`.
- **Impact:** a generic retryable runtime exception can requeue the complete
  run command after model/tool work has begun. That is not a model-attempt
  retry and risks replaying graph work across already-observed operations.
- **Required architectural fix:** F10 must introduce attempt-scoped model
  retries and a durable effect-observed barrier. The worker may retry only
  pre-dispatch infrastructure failures; any post-dispatch uncertainty must be
  reconciled, never replayed blindly.
- **Status:** resolved for generic queue replay. The worker now has an explicit
  prepare/handler-entry boundary: retryable failures before a run handler
  begins may retry, while retryable `RUN_REQUESTED` failures after entry are
  dead-lettered with `retryable=false`. Attempt-scoped provider retries,
  durable ambiguous-state reconciliation, and routing remain F10 work below.

### ARQ-006 — Production subagent dispatch bypasses the bounded handoff seam

- **Found in:** F7–F12 implementation audit; verified in
  `agent_runtime/delegation/subagents/atlas_task_tool.py` and the uncomposed
  `SubagentHandoffBuilder` / `AsyncSubagentLifecycle` abstractions.
- **Impact:** the production Deep Agents task path does not consistently bind
  a compact packet, total budget, deadline, and durable lifecycle to every
  child. This prevents F9 from proving authority, context, and retry bounds.
- **Required architectural fix:** add one F9 coordinator around the existing
  Atlas task tool and route production dispatch through it. Do not introduce a
  parallel delegation implementation.
- **Status:** open; blocking F9 parallel delegation enablement.

## Open implementation tasks

These are planned PRD slices, not independently confirmed production defects.
They remain here to make the next integration work visible without treating an
unshipped feature as a regression.

### ARQ-007 — F3 catalog is shadow-only and has no model bridge

- **Current state:** the runtime now builds a run/policy-scoped compact catalog
  from authorized `ToolCard` and `McpServerCard` records and ranks it with a
  bounded deterministic lexical ranker. Safe `search_capabilities` and
  `describe_capability` adapters now recheck the exact run subject and expiry
  on every call, but are not yet factory-wired model tools.
- **Remaining work:** bind the bridge through the runtime/factory under the
  normal budget/error-policy wrappers; add bounded top-K authorized descriptor
  expansion through the existing loader/cache, typed discovery events, and
  invocation revalidation through the existing operation gateway. Add revision
  invalidation with F8.
- **Status:** open; F3 deferred mode cannot be enabled.

### ARQ-008 — F5 admission adapter is not at the LangChain tool boundary

- **Current state:** `ToolResultAdmissionAdapter` has the correct bounded,
  fail-closed representation and the worker event projector reuses it. The
  governed `BaseTool` wrapper now admits successful returns before they go
  back to LangChain, when a desktop writer/adapter is bound to the run.
- **Remaining work:** construct and bind the adapter with the desktop
  `OffloadWriter` in the worker; project its one decision into the durable
  event/metrics path without a second offload decision; route opaque refs
  through the authorized evidence/read-back path; add an end-to-end
  current-turn `ToolMessage` test.
- **Status:** open; production binding is required to close ARQ-004.

### ARQ-009 — F10 has no attempt-level reliability control plane

- **Current state:** generic worker replay after run-handler entry is blocked.
- **Remaining work:** persist model invocation/route/attempt lineage; classify
  provider failures; permit only stream-safe provider-attempt retry/fallback;
  reconcile ambiguous post-crash attempts; aggregate cost/deadline across
  attempts; add circuit breakers and route-policy evaluation.
- **Status:** open; provider fallback remains disabled by design.

### ARQ-010 — F6 planner is not yet an execution control plane

- **Current state:** a deterministic, serial-first `BatchPlanner` now creates
  stable segments. It permits concurrency only for explicitly trusted,
  independent read/no-effect operations with disjoint opaque resource keys.
  Effects, unknown metadata, dependencies, resource conflicts, and auth-epoch
  changes are barriers.
- **Remaining work:** resolve policy precedence from real descriptors; persist
  shadow plans/events; add a bounded executor, permits, rate-limit coordinator,
  per-child gateway reauthorization, cancellation/recovery semantics, and
  curated connector opt-ins. Do not enable graph-level parallel execution
  until those controls and F1 evaluation are present.
- **Status:** open; F6 remains serial in production.
