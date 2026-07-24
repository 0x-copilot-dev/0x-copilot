# PRD-D1 — MCP convergence on the universal gateway 🎨

**Goal.** Route every MCP operation through pre-dispatch descriptor resolution,
classification, gates, and the Operation Gateway. Reads execute once and create a
surface only when presentation policy says so. Writes/destructive/unknown operations
stage exact canonical arguments before any connector dispatch and execute only through
the A5 MCP executor.

## Implementer brief

Read:

1. `../01-sdr.md` §§7, 11, sequence S6.
2. `PRD-A3-operation-gateway.md`,
   `PRD-A4-effect-stager.md`, and
   `PRD-A5-commit-coordinator.md`.
3. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py`.
4. `services/ai-backend/src/agent_runtime/capabilities/actions/`.
5. `services/ai-backend/src/agent_runtime/capabilities/mcp/annotations.py`.
6. `services/ai-backend/src/agent_runtime/surfaces_v2/emitter.py`.
7. `services/ai-backend/src/agent_runtime/surfaces_v2/gate.py`.
8. `services/ai-backend/src/agent_runtime/surfaces_v2/mcp_connector.py`.
9. `services/ai-backend/src/agent_runtime/capabilities/surfaces/`.

The critical regression is “classify after dispatch.” The new path must classify and
choose read-vs-stage before `client.call_tool`.

## Context

Current `CallMcpTool.ainvoke` dispatches first, then `WorkLedgerEmitter` classifies and
always emits `read.executed`. A write can therefore be represented dishonestly, and a
successful mapping result can create a generic surface even when no canvas is useful.
Curated `(server, tool)` specs also conflate connector identity with renderer routing.

## Interfaces consumed

- A3 Gateway/descriptors/disposition.
- A4 EffectStager.
- A5 Commit Coordinator and MCP executor.
- B3 Presentation Policy/canvas lifecycle.
- Existing MCP registry, cards, OAuth/auth gate, permission checks, annotation capture,
  citations, result offload, and surface-shaping infrastructure.

## Interfaces exposed

- `McpOperationAdapter` implementing `OperationAdapter`.
- `McpEffectExecutor` as the sole MCP mutation transport.
- curated MCP descriptor catalog keyed by normalized capability/op with stable connector
  identity as metadata, not UI renderer selection.
- MCP-specific presentation hints consumed by generic Presentation Policy.

## Design

### D1. Pre-dispatch pipeline

`CallMcpTool` becomes a thin model-facing adapter:

1. resolve server card and exact tool descriptor;
2. canonicalize arguments and create `OperationRequest`;
3. resolve/classify descriptor before client dispatch;
4. resolve authentication/access gate;
5. pass request to Operation Gateway.

Gateway branch:

- `none`/read → call `McpOperationAdapter.execute_read` exactly once;
- external/destructive/unknown → call `build_proposal`; do not call MCP;
- internal artifact intent, where allowed → Artifact Service;
- blocked gate → park without dispatch.

### D2. Descriptor and classifier precedence

Use:

1. product catalog exact server/tool;
2. trusted MCP annotations, tightening only;
3. safe default unknown/held.

Catalog may state effect class and result kind. It must not require a renderer spec.
Annotation `readOnlyHint=true` is not sufficient to downgrade a catalog write.
Unknown tools are honest: held write posture and raw metadata, not guessed read.

### D3. Read execution

Read adapter:

- performs existing permission recheck;
- resolves/creates client;
- invokes exact tool once;
- offloads large result;
- records operation completion and citations/provenance;
- returns bounded structured result to model;
- invokes Presentation Policy.

No `read.executed` event is emitted for a classified write. During compatibility, old
events may be projected from new operation events, but not produced as contradictory
truth.

### D4. Selective presentation

Curated specs become hints:

```text
PresentationHint
  result_kind: record | collection | message | raw
  preferred_archetype?
  title/subtitle/item paths?
  renderer formats?
```

They may remain keyed by server/tool as a curated optimization, but adding a new server
with an equivalent list operation must not require a hardcoded renderer route. Generic
schema/result analysis can choose record/table/raw, and the small spec-authoring model
may produce a constrained SurfaceSpec.

Rules:

- activity/scalar/transient result → no canvas by default;
- explicit user “show/open/compare” or durable record/collection → surface;
- malformed/unsupported result → raw/activity, never fabricated schema;
- tool execution is never sufficient by itself.

### D5. Write proposal

Before MCP dispatch:

- canonical JSON arguments are stored behind `proposal_ref`;
- `proposal_digest` covers exact arguments;
- target identifies connector server/tool and any stable object/precondition ref;
- safe preview/diff is derived without changing canonical args;
- EffectStager records stage;
- model receives “proposed; waiting” and stage id.

No MCP client instance is needed to stage unless a bounded read-only prepare is required
to obtain a precondition. Such reads are explicit gateway operations.

### D6. MCP executor

Move/rename `LegacyMcpEffectExecutor` to production `McpEffectExecutor`:

- `prepare`: re-resolve server/tool/auth, validate connector state, optionally fetch
  supported precondition;
- `apply`: dispatch exact canonical arguments once;
- `reconcile`: use provider idempotency/result APIs where available, otherwise honest
  indeterminate;
- provider receipt stored behind ref.

It receives no model text and cannot revise arguments.

### D7. Auth/access gates

Generalize existing ToolAccessGate into `gate.opened.v2` with
`gate_kind=authentication`. Stable gate id joins operation and server.

- first use/expired creds parks only dependent operation;
- connect resumes gateway at pre-dispatch;
- skip/cancel performs no call;
- auth success is not write approval;
- approval may happen before or after auth, but apply requires both current gate
  resolution and exact stage decision.

### D8. Model-visible result

Read:

```json
{
  "status": "completed",
  "operation_id": "op_…",
  "summary": "Fetched 12 issues.",
  "result_ref": "operation://…/result",
  "surface_id": "…"
}
```

`surface_id` is optional.

Write:

```json
{
  "status": "staged",
  "operation_id": "op_…",
  "stage_id": "stg_…",
  "summary": "Proposed updating issue ENG-42; no change has been made."
}
```

Never return success before apply.

### D9. Retirement

Under per-capability rollout:

- remove `WorkLedgerEmitter.on_tool_result` from MCP production ownership;
- remove direct MCP write dispatch from model-facing middleware;
- stop automatic mapping→surface behavior;
- keep spec generator/renderers as presentation services;
- old events/spec stores remain readable until E2.

No flag combination may cause both legacy direct dispatch and gateway dispatch for one
call.

## Implementation plan

1. Build descriptor catalog parity from current C1 catalog/annotations.
2. Implement MCP operation adapter and pre-dispatch gateway wrapper.
3. Port reads in shadow; compare result/event/disposition.
4. Enable read enforcement by connector cohorts.
5. Implement canonical-argument proposal and stage UI projection.
6. Promote compatibility MCP executor to canonical executor.
7. Generalize auth gates.
8. Enable writes by connector cohorts.
9. Remove automatic surface emission/direct writes.
10. Run connector matrix and live OAuth/write smoke.

## Test plan

### Pre-dispatch safety

- classified write/destructive/unknown records zero `client.call_tool` before approval;
- annotation/catalog contradictions use most restrictive;
- unknown tool held with honest copy;
- auth missing parks before dispatch;
- read dispatch exactly once.

### Exact apply

- staged canonical args equal applied args byte-for-byte after canonical decoding;
- revision changes invalidate approval;
- duplicate worker call produces one MCP call;
- auth expires after approval → park/fail, no call;
- uncertain provider response → reconcile/indeterminate, no blind retry.

### Presentation

- scalar read creates activity only;
- record/list creates surface only by policy;
- equivalent list tool on another connector uses generic table without a new renderer;
- malformed output raw fallback;
- no blank/duplicate tab on replay.

### Compatibility/cutover

- shadow never double-dispatches;
- old v2 fixtures replay;
- curated specs still shape supported results;
- full current connector suites and facade/client typechecks.

## Definition of done

- [ ] Classification occurs before every MCP dispatch.
- [ ] Reads execute once; writes/unknown stage before any dispatch.
- [ ] MCP executor applies exact approved canonical args.
- [ ] Surface creation is selective and transport-neutral.
- [ ] Auth gates are independent from approval.
- [ ] Direct MCP write and MCP-owned ledger/surface seams are retired for enabled
      cohorts.
- [ ] UI, effect-path, and standard DoD pass.

## Out of scope

- Non-MCP built-ins/subagents.
- Workspace/browser/sandbox adapters.
- Deleting old event/spec schemas.

## Guardrails

- Never trust MCP annotations to relax policy.
- Never classify after dispatch.
- Never stage a summarized argument set instead of canonical args.
- Never equate auth consent with effect approval.
- Never create a surface merely because output is a mapping.
