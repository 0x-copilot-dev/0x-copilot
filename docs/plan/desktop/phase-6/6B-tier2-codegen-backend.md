# Phase 6.B: tier2-codegen-backend

## Vision

A backend capability that turns a SaaS scheme + a live sample state + a layout-template choice into a complete, sandbox-safe TypeScript source string for a tier-2 `SaaSRendererAdapter`. The capability is the **Q5 constrained-template gate** from PRD §9.5.1: by emitting only from a small, well-typed set of layouts and a strict component vocabulary, downstream quality gates (Q1 schema check, Q2 AST allowlist, Q4 smoke render) have a far smaller surface to defend.

The capability runs server-side in `services/ai-backend`. It does not load, install, sandbox, or render the result — those are 6A (sandbox), 6C (lifecycle) and 6D (quality gate) on the desktop. 6B's only outputs are:

1. An `AdapterCodegenResult` returned to the immediate caller (the agent that invoked the capability).
2. A `RuntimeEventEnvelope` of type `adapter_generated` written to the run's persisted event stream so the desktop's tier-2 lifecycle (6C) can subscribe on the existing SSE channel, persist the source to `{userData}/adapters/{scheme}-v{n}.js`, and hand it to the local quality gate.

The frozen `SaaSRendererAdapter` contract from Phase 4-A is the only shape generated. The generated source uses `React.createElement(...)` rather than JSX so the downstream AST scan stays trivial (no JSX-grammar awareness in the scanner). Imports are restricted to `react` and `@enterprise-search/design-system` to match the 6A / 6D allowlist.

## Status

- Status: in-progress
- Agent slug: `phase-6-tier2-codegen-backend`
- Branch: `desktop/phase-6-tier2-codegen-backend`
- Worktree: `.claude/worktrees/agent-aca4b164e3f48273b`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-6/6B-tier2-codegen-backend.md` — this file.
- `services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/__init__.py` — package init + re-exports.
- `services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/models.py` — Pydantic models (`LayoutTemplate`, `SampleState`, `AdapterCodegenResult`).
- `services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/templates.py` — the four layout template builders.
- `services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/capability.py` — `RenderAdapterGenerator` capability with the async `generate` entrypoint that builds the source, validates it locally against the same allowlist 6A/6D will enforce, and emits the `adapter_generated` event when bound to a `RuntimeEventProducer`.
- `services/ai-backend/src/agent_runtime/capabilities/__init__.py` — extend `__all__` to surface the new subpackage.
- `services/ai-backend/src/runtime_api/schemas/common.py` — add `ADAPTER_GENERATED = "adapter_generated"` to `RuntimeApiEventType`. The event projects to `RuntimeActivityKind.EVENT` (default), with no special projection branch — payload pass-through is fine since the desktop reads `payload.adapter_source` directly.
- `services/ai-backend/tests/unit/agent_runtime/capabilities/render_adapter_generator/__init__.py` — empty test package marker.
- `services/ai-backend/tests/unit/agent_runtime/capabilities/render_adapter_generator/test_capability.py` — round-trip tests for all four templates: codegen → local AST allowlist scan → exported-symbol check → forbidden-pattern check. Plus the event-emission path.

**Out of scope** (do NOT touch):

- `apps/desktop/**` — 6A owns sandbox, 6C owns lifecycle, 6D owns the local quality gate. The capability does not write to disk and does not know about `{userData}/adapters/`.
- `packages/chat-surface/**`, `packages/surface-renderers/**` — adapters land at runtime via the lifecycle pipeline; this agent never edits compiled JS in those packages.
- `services/backend/**`, `services/backend-facade/**` — no facade routing or backend table is needed for local-only tier-2 (Phase 7 adds the server-side registry; that's a separate agent).
- The actual Web-Worker sandbox or `vm` AST scanner used at install time — those are 6A/6D. This agent's local allowlist check is a defensive guard that ensures the template can never produce source that fails 6A/6D, not a replacement for them.

## Functional requirements

- [ ] FR-1: `LayoutTemplate` is a `StrEnum` with exactly four values: `FORM`, `TABLE`, `KANBAN`, `DEFINITION_LIST`. Any unknown value is rejected by Pydantic validation with a typed safe message.
- [ ] FR-2: `SampleState` is a Pydantic model that validates the untrusted `sample_state` dict passed by the agent. Keys must be non-empty strings; values must be JSON-serialisable scalars (`str | int | float | bool | None`) or homogeneous lists/objects of those (depth limited; total fields limited). Each template chooses which subset of fields it consumes; unused fields are tolerated but not echoed into the generated source.
- [ ] FR-3: `AdapterCodegenResult` is a Pydantic model with: `scheme: str`, `layout: LayoutTemplate`, `schema_version: PositiveInt` (always 1 in Phase 6), `adapter_source: str`. The field names match the desktop's expectation when reading the event payload.
- [ ] FR-4: `RenderAdapterGenerator.generate(scheme, sample_state, layout_template)` returns `AdapterCodegenResult`. The async method does not await anything heavy and never makes network calls; it is async-shaped to fit the capability pattern used by the rest of `agent_runtime/capabilities/`.
- [ ] FR-5: Each template produces a single complete TypeScript source file with:
  - Two `import` statements at the top, each from one of `react` / `@enterprise-search/design-system`. No other module specifiers.
  - One named `export const adapter: SaaSRendererAdapter<...>` declaring `scheme`, `matches`, `renderCurrent`, `renderDiff`, `metadata`.
  - Two named `export const renderCurrent` and `export const renderDiff` arrow functions that are the same functions assigned to `adapter.renderCurrent` / `adapter.renderDiff`. (Two distinct export paths so the desktop can either import the whole adapter or the individual render functions for unit smoke tests in 6D.)
  - `metadata.origin === "agent-generated"`, `metadata.schemaVersion === 1`, `metadata.generatedAt` set to the capability call's UTC ISO timestamp, `metadata.generatorModel` set to a constant `"render-adapter-generator/v1"`.
  - No JSX, no template literals containing `${}` interpolations, no comments, no `var`, no `eval`, no `Function`, no `window` / `document` / `localStorage` / `fetch`, no `import()` expressions.
  - All JSX-equivalent output uses `React.createElement("tagName", props, ...children)` only.
- [ ] FR-6: When `RenderAdapterGenerator` is constructed with a `RuntimeEventProducer` and a `RunRecord`, `generate(...)` additionally appends an `ADAPTER_GENERATED` event to the run stream. Payload includes: `scheme`, `layout`, `schema_version`, and `adapter_source`. The event's `activity_kind` resolves through the default `RuntimeActivityKind.EVENT` projection so the desktop's existing SSE consumer sees it on the same channel that already replays tool events.
- [ ] FR-7: Before returning, the capability runs a local `AdapterAllowlistAuditor` that scans the produced source for forbidden patterns: any `import` specifier outside the allowlist, occurrences of `window` / `document` / `localStorage` / `sessionStorage` / `fetch` / `XMLHttpRequest` / `EventSource` / `WebSocket` / `eval` / `new Function` / `import(` / `require(`. If the auditor finds anything, the capability raises a typed `AdapterCodegenError` rather than returning broken source — this is a defensive contract: the templates themselves must never produce such patterns.
- [ ] FR-8: `Tests`:
  - For each of the four templates, generate a source string and assert:
    - Substring `export const adapter` present.
    - Substring `export const renderCurrent` present.
    - Substring `export const renderDiff` present.
    - Substring `React.createElement(` present.
    - Substring `metadata: {` present.
    - Imports limited to `react` and `@enterprise-search/design-system` (regex scan).
    - None of the forbidden patterns from FR-7 are present.
    - The generated source parses as a sequence of TypeScript top-level statements by a permissive regex (we are not running a real TS parser; we mirror what the desktop's AST scanner enforces structurally, which is what 6A/6D will own at runtime).
  - The `AdapterAllowlistAuditor` raises on a manually-crafted bad source containing each forbidden pattern.
  - When an event producer is bound, `generate(...)` calls `producer.append_api_event` exactly once with `event_type=ADAPTER_GENERATED` and the right payload keys. When no producer is bound, the call still returns the result without raising.
  - `LayoutTemplate` accepts only the four documented values; arbitrary strings raise `ValidationError`.
  - `SampleState` rejects deeply-nested or oversized inputs with a typed safe message.

## Non-functional requirements

- Python 3.13. `from __future__ import annotations` everywhere.
- Pydantic v2 at every boundary. No `Any` in any model field. No long-lived `dict[str, Any]` domain state.
- All public behavior lives in classes; no module-level helper functions (per `services/ai-backend/CLAUDE.md`).
- No internal exception detail leaks through `AdapterCodegenError.safe_message` — only the safe layout name and a generic failure reason.
- The generator never reads the live MCP tool; the caller (the agent) passes `sample_state` as a validated dict.
- No comments in either Python or generated TypeScript by default.

## Interfaces consumed

- `agent_runtime.execution.contracts.RuntimeContract` — Pydantic base.
- `agent_runtime.api.events.RuntimeEventProducer` — for the optional event emission path.
- `runtime_api.schemas.RunRecord` — passed to `producer.append_api_event`.
- `runtime_api.schemas.common.RuntimeApiEventType` — extended with `ADAPTER_GENERATED`.

## Interfaces produced

```python
# services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/__init__.py
from agent_runtime.capabilities.render_adapter_generator.capability import (
    AdapterCodegenError,
    RenderAdapterGenerator,
)
from agent_runtime.capabilities.render_adapter_generator.models import (
    AdapterCodegenResult,
    LayoutTemplate,
    SampleState,
)
```

The capability is consumed by the agent's tool registry through the existing capability registration pattern; subsequent phases (or the agent prompt curation) bind it as a callable tool when the lifecycle pipeline requests generation.

## Wire-level event shape (consumed by 6C)

```
event_type: "adapter_generated"
payload: {
  "scheme": "<urn-style scheme>",
  "layout": "form" | "table" | "kanban" | "definition-list",
  "schema_version": 1,
  "adapter_source": "<complete TS source>"
}
```

The desktop's tier-2 lifecycle in 6C subscribes to this event type on the run's SSE stream, persists `adapter_source` to `{userData}/adapters/{scheme}-v{schema_version}.js` (after transpilation/AST scan), and triggers the smoke-render path.
