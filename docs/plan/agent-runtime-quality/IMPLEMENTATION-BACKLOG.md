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

## Open

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
- **Status:** open; blocking F5 context enforcement.

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
- **Status:** open; blocking F10 fallback enablement.

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
