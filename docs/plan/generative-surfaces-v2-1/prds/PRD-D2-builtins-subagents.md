# PRD-D2 — Built-in capabilities and subagents

**Goal.** Register every model-visible built-in and delegated subagent operation with
the universal capability catalog. Normalize pure compute, internal reads, artifact
publication, and effect proposals; build a complete operation tree and usage
attribution; ban bespoke effects and surface emission from built-ins/subagents.

## Implementer brief

Read:

1. `../01-sdr.md` §§7.2, 7.4, 16.
2. `PRD-A3-operation-gateway.md`,
   `PRD-A5-commit-coordinator.md`, and
   `PRD-B1-agent-authored-artifacts.md`.
3. `services/ai-backend/src/agent_runtime/execution/factory.py`.
4. `services/ai-backend/src/agent_runtime/capabilities/tools/`.
5. `services/ai-backend/src/agent_runtime/capabilities/interpreter/`.
6. `services/ai-backend/src/agent_runtime/delegation/subagents/`.
7. `services/ai-backend/src/agent_runtime/observability/usage_meter.py`.
8. `services/ai-backend/src/runtime_worker/stream_tools.py`.

Do not include remote sandbox provider behavior or browser effect behavior; D3/D4 own
those adapters.

## Context

MCP is only one tool transport. The runtime also has `ask_a_question`,
`suggest_mcp_connector`, `stage_rowset_write`, dynamic tool loading, code mode, prior
results, skills, and subagent delegation. Today they get generic tool events but not one
consistent operation/effect/presentation contract.

## Interfaces consumed

- A3 descriptors/gateway/conformance gate.
- A4/A5 staging/commit for effectful built-ins.
- B1 publication and subagent artifact attribution.
- B3 presentation lifecycle.
- Existing tool registry/cards/policy gates and subagent correlation ids.

## Interfaces exposed

- complete checked-in built-in descriptor catalog;
- `BuiltinOperationAdapter`;
- `SubagentOperationAdapter`;
- operation-tree projection:

```text
OperationNode
  operation_id, parent_operation_id?, producer
  capability, op, status, started_at, completed_at?
  artifact_ids[], stage_ids[], usage_totals
```

- immutable `UsageAttributionEdge` for usage learned before an artifact/stage id exists.

## Design

### D1. Inventory and descriptors

Inventory every model-visible callable assembled in `_model_visible_tools` and dynamic
load paths. At minimum:

- ask a question;
- load tool;
- suggest MCP connector;
- publish artifact;
- stage rowset write compatibility;
- run code mode;
- prior-result access;
- skill virtual/file operations;
- task/subagent delegation;
- sandbox and browser placeholders owned by D3/D4;
- workspace filesystem operations owned by C3;
- MCP wrapper owned by D1.

Each has one descriptor or a dated temporary exemption. Default unknown/held.

### D2. Pure/internal built-ins

`ask_a_question`, catalog suggestions, bounded prior-result reads, and local
calculation are `effect_class=none` or `internal_reversible` as appropriate.

They:

- emit operation events;
- execute once;
- return bounded summaries/results;
- do not create a surface by default;
- create an artifact only through explicit ArtifactIntent/publication.

### D3. Code mode

`run_code_mode` remains a constrained Python-subset compute tool:

- no filesystem/network/imports;
- external function calls each create child operations and independently pass gateway
  policy/budget;
- JSON result stays operation result by default;
- explicit result publication creates an artifact;
- code supplied to the interpreter is not itself automatically an artifact;
- interpreter snapshots/usage are linked to operation id.

No interpreter callback can call an external function outside the gateway.

### D4. Effectful built-ins

Any built-in capable of changing external/product state must:

- build a canonical proposal;
- call EffectStager;
- return staged result;
- have a registered A5 executor if it is launch-enabled.

`stage_rowset_write` becomes a thin compatibility alias over generic proposal kind
`row_set`, then is retired in E2 when callers use the general path.

Built-in code may not import an executor registry or transport client.

### D5. Subagent operation tree

Delegation itself:

- parent operation `capability=subagents`, `op=delegate`;
- subagent task id maps deterministically to child root operation;
- every child operation carries `parent_operation_id`;
- capability set is intersection of parent grant/policy and subagent definition;
- subagent cannot widen tools, grants, approval policy, or tenant scope;
- cancellation cascades to active child operations but not already-applied effects;
- child artifacts/stages remain canonical and visible to parent.

Use existing `supervisor_task_call_id`; remove FIFO/guessing fallbacks where present.

### D6. Subagent artifacts

- explicit `publish_artifact` works unchanged inside subagent context;
- authorship `subagent`, source/task ref, parent operation;
- multiple artifacts per task allowed;
- parent can open/reference but cannot rewrite history;
- malformed/oversize result envelope does not delete already-published artifacts.

### D7. Usage attribution

Every model/interpreter/shaping/subagent call records once through UsageMeter.
Add optional `operation_id`. If artifact/stage id becomes known later, append immutable:

```text
UsageAttributionEdge
  usage_record_id
  operation_id
  artifact_id?
  stage_id?
  relationship: produced | revised | proposed | shaped
```

Do not update historical usage rows in place. Rollups join edges and deduplicate by
usage record id.

### D8. Presentation

- pure compute → chat/activity;
- artifact → B2/B3 presentation;
- effect proposal → stage surface/card;
- subagent progress → Agents/activity rail;
- no bespoke `surface.created` emission from a built-in or subagent module.

### D9. Architecture gate

Static/runtime conformance:

- every callable has descriptor;
- no direct network/MCP/browser/workspace/sandbox effect client in built-in/subagent
  modules except their designated adapters;
- no direct canonical surface-event emitter;
- no model call outside approved model construction/UsageMeter seams;
- subagent capabilities are subset of parent.

Use planted canaries to prove each gate fails.

## Implementation plan

1. Generate inventory report and descriptor catalog.
2. Wrap built-ins at registry/assembly boundary with Operation Gateway.
3. Adapt pure/internal tools.
4. Adapt code mode and external-function child operations.
5. Adapt rowset staging compatibility.
6. Wrap subagent dispatch/result with operation ids.
7. Wire subagent artifact/stage attribution.
8. Add usage attribution edges/rollups.
9. Add operation-tree projectors/API types.
10. Enforce architecture gate and delete exemptions before E2.

## Test plan

### Inventory/conformance

- every current tool resolves descriptor;
- dynamically loaded tool defaults unknown/held until descriptor loaded;
- planted direct client/surface emitter/unmetered model call fails gate.

### Built-ins/code mode

- pure calculation creates operation, no surface/artifact by default;
- explicit publication creates artifact;
- code-mode external calls each traverse gateway;
- effectful rowset stages, never applies inline;
- cancellation/timeout usage counted once.

### Subagents

- two concurrent subagents correlate to correct parent operations;
- capability narrowing cannot be widened by prompt/result;
- child artifacts/stages/usage attributed correctly;
- cancellation and retry do not duplicate artifacts/effects/usage;
- cross-tenant/source leakage denied.

### UI/projections

- operation tree deterministic on replay;
- Agents rail progress/pending work correct;
- Focus cards compact; Studio subjects open correctly.

## Definition of done

- [ ] Every built-in/subagent callable has a descriptor or valid temporary exemption.
- [ ] Pure compute and internal reads use gateway without unwanted surfaces.
- [ ] Effectful built-ins stage and have no direct executor.
- [ ] Subagent operation tree/capability narrowing is authoritative.
- [ ] Artifact/stage/usage attribution is complete and retry-safe.
- [ ] No bespoke surface emission remains.
- [ ] Standard DoD passes.

## Out of scope

- Remote sandbox implementation.
- Browser side-effect implementation.
- MCP details.
- Workspace host commit.

## Guardrails

- No tool-name-only policy exceptions.
- No subagent capability widening.
- No code-mode external callback outside gateway.
- No inferred artifact from interpreter code/result.
- No in-place rewrite of usage history.
