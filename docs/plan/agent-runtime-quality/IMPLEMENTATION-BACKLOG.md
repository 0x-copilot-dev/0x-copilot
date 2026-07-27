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

### ARQ-004 — Desktop tool-result offload happened after model context admission

- **Found in:** F2–F6 implementation audit; verified in
  `runtime_worker/tool_result_offload.py`.
- **Impact:** offload originally reduced only persisted event/UI payload size;
  one oversized result could still reach the current model context.
- **Architectural fix:** the desktop file-runtime now constructs one shared
  `FileOffloadWriter → ToolResultAdmissionAdapter → ToolResultOffloader`
  pipeline per handler. An admission-carrying empty-budget guard is active even
  when no budget rows exist, so every successful guarded tool result is bounded
  before its `ToolMessage`. The stream projector consumes that exact bounded
  decision once, and approval resume uses the same boundary. Writer failures
  remain fail-closed; non-file behavior is unchanged.
- **Status:** resolved for the desktop file runtime; regression coverage proves
  the model sees bounded content, the event gets the same reference, and the
  original content is stored once in the content-addressed object store.

## Open

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

- **Current state:** the desktop file runtime now binds the adapter at the
  common pre-`ToolMessage` wrapper even with zero tool-budget rows. Its
  run-keyed bounded projection is consumed once by the event path, and the
  approval-resume path shares that behavior. The desktop regression test proves
  a large raw result is not returned to the model while the exact bytes remain
  in the content-addressed store.
- **Remaining work:** add the equivalent governed writer for future hosted/B2C
  runtime stores; turn the one admission decision into a durable raw-free
  metrics/event fact; route opaque refs through the general authorized evidence
  reader; and add full graph-level current-turn coverage across every tool type.
- **Status:** open only for cross-store F5 completion; ARQ-004 is closed for
  the desktop runtime.

### ARQ-009 — F10 attempt policy is not attached to live provider invocation

- **Current state:** generic worker replay after run-handler entry is blocked.
  The pure F10 control plane now constructs versioned deployment,
  requirements, credential, and budget contracts; deterministically derives
  route eligibility and stable exclusions; classifies sanitized failures; and
  admits bounded attempts. It denies whole-run replay, retries after visible
  output, and ambiguous provider state.
- **Remaining work:** adapt the current model catalog/enablement and verified
  region/privacy/BYOK facts into deployment descriptors; persist invocation,
  route, and attempt lineage; add provider-specific error adapters and
  attempt-scoped invocation/usage metering; preserve streaming discontinuities;
  reconcile ambiguous post-crash attempts; add circuit health and
  F1-qualified equivalent routes. Worker retries must remain outside this
  policy and must never replay a run.
- **Status:** open; no live provider fallback or routing behavior has changed.

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

### ARQ-011 — F8 descriptor freshness has no authoritative revision feed or session reuse

- **Current state:** `RevisionAwareMcpDiscoveryCache` layers opaque
  subject-scoped revisions, a separate maximum-staleness ceiling, exact
  invalidation, and cancellation-safe per-key load coalescing over the existing
  TTL/LRU cache. It fails closed for legacy entries without revision metadata.
- **Remaining work:** add a backend-owned authoritative revision source and
  durable cursor; coordinate revision invalidation with the F3 catalog;
  introduce an in-flight invalidation generation barrier before wiring a feed;
  add cache/refresh metrics and diagnostics; and implement or explicitly
  verify backend-owned remote MCP session pooling, isolation, keepalive, and
  reconnect behavior. ai-backend must not own credentials or a remote session
  pool.
- **Status:** open; the wrapper is not yet wired into descriptor loading and
  provides no cross-process freshness convergence.

### ARQ-012 — F9 coordinator is not on the production Atlas dispatch path

- **Current state:** `DelegationCoordinator` now creates compact,
  transcript-free packets, validates dependency DAGs, reserves aggregate
  budgets, enforces server-derived depth/child/deadline constraints, and emits
  deterministic topological waves without dispatching a child.
- **Remaining work:** route the existing Atlas task tool through this one
  coordinator; derive remaining budget/deadline from server state and reserve
  them atomically with reconciliation; persist admission, packet, plan, and
  lifecycle facts; reauthorize evidence refs; verify typed child results and
  resolve contradictions; and recover idempotently after restart. Do not add a
  second subagent implementation.
- **Status:** open; this is the implementation slice required to close
  ARQ-006.

### ARQ-013 — F11 patch plans are not an atomic workspace edit workflow

- **Current state:** `WorkspacePatchSetValidator` admits a closed patch-plan
  vocabulary with a frozen target set, exact base preconditions, immutable
  content refs, case-alias protection, deterministic operation order, and
  non-overlapping digest-bound hunk metadata. It neither reads content nor
  mutates the workspace.
- **Remaining work:** build target discovery and bounded edit context; resolve
  immutable refs; re-verify byte spans/anchors and apply a complete patch set
  atomically through `WorkspaceOverlayStorePort.append_revision`; apply effect,
  policy, and budget admission; run cheap validation profiles; persist
  structured diagnostics; bound repairs; and project review/evaluation facts.
  D3 integration remains blocked on the C1 snapshot/export/import authority
  prerequisites and must not be enabled before they exist.
- **Status:** open; F11's production edit path does not exist yet.

### ARQ-014 — F4 policy selection and plans are not bound to production runs

- **Current state:** the existing F4 module now derives a closed,
  revision-bound `TaskPolicySelection` from server-derived signals, prevents
  effect/delegation facts from being downgraded by a selected route, and
  supplies a bounded `unknown.general` fallback. `RunToolPlan` is bound to that
  selection and validates public steps and success-evidence requirements. The
  optional duplicate controller remains behind the common guarded-tool seam.
- **Remaining work:** construct the resolver from a deployment-owned versioned
  profile bundle during run creation; persist selection and plan facts and
  reconstruct them after restart; bind the selected controller/fingerprint to
  every governed tool guard; emit profile, plan, feedback, and budget events;
  enforce the planning/model-turn/cost/wall-time hierarchy; add a progress
  projector; and cover rollout/F1 quality evaluation. Profile selection must
  never become an authorization or capability-discovery path.
- **Status:** open; no production run currently selects, persists, or displays
  a task policy plan.

### ARQ-015 — F7 dataflow has no executor, evidence path, or model surface

- **Current state:** F7 now has a source-free, closed `dataflow.v1` AST and a
  deterministic validator. It checks canonical DAG/reachability, expression
  and static-call bounds, exact opaque capability bindings with descriptor
  revisions, no-effect-only invocation, and explicit trusted F6 concurrency
  metadata for a batch. The plan digest includes the trusted descriptor facts.
- **Remaining work:** add quota-enforcing execution with durable
  snapshot/resume/cancellation; re-resolve authorized input and capability
  bindings through F3/F8 and A3 for every child call; replace coarse static
  types with bounded schema checking; integrate the F6 executor and rate
  limits; store intermediates, output, and evidence manifests through existing
  runtime refs/offload; emit typed events/usage/citation/audit facts; then add
  a feature-gated `run_dataflow` tool and model contract. Keep mutation batch
  generation behind a separately reviewed A4/A5 flag.
- **Status:** open; this validator does not execute code, perform I/O, or
  expose an agent tool.

### ARQ-017 — F12 verifier is not yet part of authoritative finalization

- **Current state:** F12 now has a content-safe answer envelope, requirement,
  claim, evidence, conflict, and trusted-fact contract plus a deterministic
  verifier. The report carries only safe IDs/digests/reason codes and checks
  requirement completion, material support, authorization, revocation,
  freshness, locator validity, conflicts, and externally supplied secret-scan
  findings.
- **Remaining work:** compile requirements from F4; add structured-envelope
  parsing/tagged fallback and synthesis prompt wiring; resolve/re-authorize
  evidence and locators in batches; buffer and verify before authoritative
  final-response persistence; add one bounded repair controller plus degraded
  and blocked finalizers; project citations to shared surfaces; persist the
  envelope/ledger/report through runtime ports with crash replay/deletion; and
  connect effect receipts, secret scanning, F1 evaluation, metrics, flags, and
  fault/performance tests.
- **Status:** open; this module performs no I/O, model invocation, or response
  publication.
