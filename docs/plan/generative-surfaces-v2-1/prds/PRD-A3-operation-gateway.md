# PRD-A3 — Universal Operation Gateway

**Goal.** Introduce a single, descriptor-driven Operation Gateway that normalizes every
agent capability invocation, classifies its effect semantics, selects a result
disposition, and emits canonical operation events. It initially runs in shadow/compat
mode: existing execution and approval paths remain authoritative until their owning
cutover PR.

## Implementer brief

Read:

1. `../01-sdr.md` §§5.1, 5.3, 5.5, 7, 11, 16.
2. `PRD-A1-artifact-effect-contracts.md` and `PRD-A2-artifact-repository.md`.
3. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py`.
4. `services/ai-backend/src/agent_runtime/capabilities/actions/`.
5. `services/ai-backend/src/agent_runtime/capabilities/tools/tool_use_enforcement.py`.
6. `services/ai-backend/src/agent_runtime/execution/factory.py`.
7. `services/ai-backend/src/agent_runtime/capabilities/desktop/`.
8. `services/ai-backend/src/agent_runtime/observability/usage_meter.py`.

The immediate implementation target is a pure gateway plus shadow adapters and
conformance gates. Do not reroute writes in this PR.

## Context

The current runtime has several unrelated control points:

- MCP calls classify in MCP middleware;
- workspace mutations use Deep Agents filesystem permissions;
- built-ins are assembled directly;
- draft persistence is an internal backend;
- browser/sandbox capabilities have their own semantics.

That means “tool called” is currently overloaded to mean execution, safety, and
presentation. A universal operation abstraction separates those axes and creates one
structural seam where later PRs can prove that all external effects stage first.

## Interfaces consumed

- A1 operation/artifact/effect contracts and canonical digest rules.
- A2 Artifact Service for dispositions that explicitly create internal artifacts.
- Existing C1 classifier/catalog logic under
  `agent_runtime/capabilities/actions/`.
- Existing policy snapshot and UsageMeter infrastructure.
- Existing per-run event producer and ContextVar binding patterns.

## Interfaces exposed

Create `services/ai-backend/src/agent_runtime/capabilities/operations/`:

```text
contracts.py
descriptors.py
catalog.py
classifier.py
disposition.py
gateway.py
context.py
errors.py
conformance.py
```

Stable interfaces:

```python
class OperationGateway:
    async def invoke(
        self,
        request: OperationRequest,
        adapter: OperationAdapter,
    ) -> OperationDisposition: ...

class OperationAdapter(Protocol):
    async def execute_read(self, request: OperationRequest) -> OperationRawResult: ...
    async def build_proposal(self, request: OperationRequest) -> ProposedEffect: ...

class OperationDescriptorRegistry:
    def resolve(self, capability: str, op: str) -> OperationDescriptor | None: ...

class PresentationDispositionPolicy:
    def decide(request, descriptor, result_summary) -> PresentationDecision: ...
```

`OperationContext.bind_for_run(...)` exposes verified run identity, policy snapshot,
ledger emitter, Artifact Service, and the feature-mode setting without global mutable
state.

## Design

### D1. Gateway pipeline

The gateway executes this deterministic pipeline:

1. validate and canonicalize the request;
2. resolve descriptor;
3. classify effect semantics fail-closed;
4. emit `operation.requested`;
5. resolve required access/auth/grant gates;
6. for `none` or `internal_reversible`, invoke the allowed adapter method;
7. for external/unknown effects, request a proposal but do not apply;
8. persist any explicit artifact intent through Artifact Service;
9. run presentation disposition;
10. emit completion/failure and return an `OperationDisposition`.

In shadow mode steps 4, 9, and comparison telemetry run, but the legacy caller remains
authoritative and no extra execution or stage occurs.

### D2. Descriptor registry

Descriptors are data, not conditional branches. They declare:

- capability namespace and operation name;
- executor kind;
- effect class;
- whether result bytes can become an artifact;
- whether the operation supports prepare/reconcile;
- gates required before read/proposal/apply;
- size and timeout budgets;
- whether unknown arguments change classification.

Registry precedence:

1. product-owned exact descriptor;
2. curated connector/tool catalog;
3. trusted provider annotation, allowed only to tighten;
4. safe default.

The safe default is `effect_class=unknown`, no automatic apply, no automatic canvas
surface, raw activity summary only. Provider annotations may change read to write, but
never write/destructive to read.

### D3. Classification

Refactor or wrap the existing
`agent_runtime/capabilities/actions/ActionClassifier`; do not duplicate its catalog.
Return:

```text
effect_class, basis, confidence, descriptor_version, reasons[]
```

Rules:

- exact product descriptor wins;
- destructive stays destructive regardless of user allow-always;
- unknown follows the write/held axis;
- classification is pure and total;
- raw argument values never enter reasons or public events.

In shadow mode compare legacy classification with gateway classification and emit a
metric, not a behavior change:

```text
operation_gateway_classification_mismatch_total{
  capability, op, legacy_class, gateway_class
}
```

### D4. Canonical request construction

Every producer adapter creates `OperationRequest` before execution. Structured
arguments are stored behind `canonical_args_ref`; the public event carries only the
digest. The gateway receives verified `run_id` and producer identity from bound
context, not from model-controlled arguments.

Parent-child attribution:

- subagent request has `producer=subagent`;
- `parent_operation_id` joins it to the delegating operation;
- recursive depth and call budget come from existing runtime guards.

### D5. Result disposition

Result disposition is independent of whether an operation executed:

```text
artifact        explicit ArtifactIntent or user promotion
canvas          durable/revisitable subject with a useful renderer
chat_card       compact rich result appropriate in Focus/chat
activity_only   progress/provenance only
none            no user-visible object beyond final narrative
```

Rules in this PR:

- explicit `ArtifactIntent` may create an internal artifact;
- no artifact intent means no artifact, even if output contains a code fence;
- a read is not automatically a canvas surface;
- an external-effect proposal becomes a stage subject, not an artifact unless it also
  carries explicit artifact intent;
- raw/unknown result produces an honest activity entry and bounded raw fallback.

Shadow mode records the predicted disposition for comparison but does not create a
second legacy surface.

### D6. Gateway mode

Add one setting with closed values:

```text
OPERATION_GATEWAY_MODE=off | shadow | enforce
```

Initial default is `off`. A3 supports `shadow`; `enforce` may be accepted by the parser
but must refuse startup unless the required executor/stage dependencies are wired.
Later PRs enable enforcement per capability through a capability allowlist.

Settings must be in runtime settings, not read ad hoc at every call site.

### D7. Producer adapters in this PR

Add non-authoritative adapters at the narrowest existing seams:

- MCP: around `CallMcpTool.ainvoke`;
- workspace filesystem: around
  `DesktopWorkspaceBackend.awrite/aedit/adelete/amove/mkdir`;
- model final output: observe typed artifact content parts only;
- built-ins: at tool assembly wrappers;
- subagents: at delegation dispatch/result.

Shadow adapters must never:

- execute the underlying operation twice;
- mutate the returned model-visible value;
- create an external stage;
- change an approval decision;
- force a canvas tab.

### D8. Return-to-agent contract

Normalize safe summaries:

```text
succeeded: "Completed <display operation>."
artifact:  "Created <kind> artifact '<title>' (artifact_id=...)."
staged:    "Proposed <display operation>; waiting for review (stage_id=...)."
blocked:   "Needs <gate>; no external change was made."
failed:    "<safe failure>; no external change was made."
```

The full external result stays behind a ref. The model sees a bounded structured
summary and ids it can reference in follow-up operations.

### D9. Observability and metering

The gateway itself makes no model call. Emit:

- request/completion/failure counters;
- classification mismatch;
- disposition mismatch;
- latency by capability/op/effect class;
- stage-required count;
- artifact-intent count.

No metric label may contain artifact titles, paths, connector arguments, or user text.

## Implementation plan

1. Add operation contracts/interfaces and unit tests.
2. Wrap the existing classifier/catalog behind the new classifier port.
3. Add descriptor registry and curated descriptors for current built-ins, MCP wrapper,
   workspace operations, draft/artifact publication, subagents, sandbox, and browser
   seams that exist in the repo.
4. Add `OperationContext` run binding.
5. Add pure disposition policy and exhaustive table tests.
6. Add gateway with fakes for read, proposal, artifact, gates, and event emission.
7. Add `off|shadow|enforce` setting and startup validation.
8. Add shadow probes at existing seams, guarded by mode.
9. Add architecture/conformance test described in D10.

### D10. Architecture conformance gate

Scan model-facing capability registration and require every callable operation to have
exactly one descriptor or an explicit temporary exemption containing:

- owner;
- expiry date;
- reason;
- safe default classification.

The test fails for expired exemptions and for descriptors whose executor is not in the
closed executor registry. Store exemptions in one checked-in data file; do not scatter
comments.

## Test plan

### Pure gateway

- all effect classes follow the expected branch;
- external/unknown never call an apply method;
- explicit artifact intent creates one artifact;
- a fenced-code string with no intent creates zero artifacts;
- adapter failure emits `operation.failed` with safe code;
- cancellation emits cancelled outcome and propagates cancellation;
- same operation id/digest is idempotent; digest mismatch conflicts.

### Shadow safety

- every seam invokes legacy execution exactly once;
- returned bytes/events are byte-identical with gateway off vs shadow;
- shadow never creates artifact/stage unless explicitly configured to observe an
  already-created canonical artifact;
- an exception in telemetry/shadow comparison never fails the user operation.

### Classification

- catalog wins over contradicting provider annotation;
- annotation can tighten read to write;
- unknown is held;
- destructive cannot become auto;
- no raw args appear in event/log snapshots.

### Conformance

- plant an unregistered fake tool and prove the gate fails;
- plant an expired exemption and prove the gate fails;
- all current registered capabilities resolve a descriptor or valid exemption.

## Definition of done

- [ ] Gateway and all exposed interfaces are implemented.
- [ ] Descriptor coverage gate includes every current capability registration seam.
- [ ] Off and shadow modes are byte-compatible with existing execution.
- [ ] Shadow proves no duplicate calls or stages.
- [ ] Classification/disposition matrix is exhaustive.
- [ ] No new external write path exists.
- [ ] No new model call exists.
- [ ] Standard DoD passes.

## Out of scope

- Generic effect commit execution.
- Replacing MCP or workspace approval behavior.
- Artifact renderers.
- Default-on gateway enforcement.

## Guardrails

- Gateway owns orchestration, not provider-specific execution.
- Descriptors are declarative and reviewed; model text cannot alter them.
- Never infer artifact intent from Markdown/code fences.
- Never classify an unknown operation as read.
- Never use shadow mode to create a second source of truth.
