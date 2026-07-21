# PRD-01 — Surface contract: schema, types, event (Wave 0, serial)

**Goal:** freeze every cross-PRD interface for the generative-UI effort in one PR: the SurfaceSpec JSON Schema (SSOT), the TS types + guards, the pydantic mirror, and the new `surface_spec_generated` event type. Nothing renders yet; this PR is contracts + tests only.

**Why serial:** every Wave-1+ PRD consumes these shapes. They are read-only after merge; changes go through an amendment to this PRD.

## Scope — files

| File                                                                                | Change                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `packages/service-contracts/src/copilot_service_contracts/surface_spec.schema.json` | NEW — the SurfaceSpec JSON Schema (SSOT). Sits beside `adapter_allowlist.json` (same shared-JSON precedent)                                                                                                                                                                                                                                                                                                  |
| `packages/service-contracts/src/copilot_service_contracts/surface_spec.py`          | NEW — constants: `SURFACE_SPEC_SCHEMA_PATH`, `SURFACE_SPEC_VERSION = 1`, `SURFACE_ARCHETYPES` tuple                                                                                                                                                                                                                                                                                                          |
| `packages/service-contracts/src/copilot_service_contracts/surface_spec.README.md`   | NEW — 1-page doc of the schema + versioning policy (mirror the adapter_allowlist README style)                                                                                                                                                                                                                                                                                                               |
| `packages/api-types/src/index.ts`                                                   | EXTEND — `SurfaceArchetype` union, `SurfaceSpec`, `SurfaceEnvelope`, `SurfaceSpecGeneratedPayload`; optional `surface?: SurfaceEnvelope` member on `ToolResultPayload` and `DraftUpdatedPayload`; `surface_spec_generated` added to `RuntimeApiEventType` union and `RuntimeEventPayloadByType`; runtime guards `isSurfaceEnvelope`, `isSurfaceSpec` following the existing `isRuntimeEventEnvelope` pattern |
| `services/ai-backend/src/runtime_api/schemas/common.py`                             | EXTEND — `RuntimeApiEventType.SURFACE_SPEC_GENERATED = "surface_spec_generated"`                                                                                                                                                                                                                                                                                                                             |
| `services/ai-backend/src/runtime_api/schemas/events.py`                             | EXTEND — projector branch: `surface_spec_generated` → `RuntimeActivityKind.EVENT`, display title "Prepared a view", payload allow-list passes `{surface_uri, archetype, spec, spec_version, generator_model, skill_version}`                                                                                                                                                                                 |
| `services/ai-backend/src/agent_runtime/capabilities/surfaces/__init__.py`           | NEW package dir                                                                                                                                                                                                                                                                                                                                                                                              |
| `services/ai-backend/src/agent_runtime/capabilities/surfaces/spec_models.py`        | NEW — pydantic `SurfaceSpec`, `SurfaceField`, `SurfaceLink`, `SurfaceEnvelope` models; `validate_surface_spec(dict) -> SurfaceSpec` that ALSO checks the raw dict against the service-contracts JSON Schema (jsonschema lib already in the venv? if not, hand-roll the few checks — no new heavy deps)                                                                                                       |

## The contract (normative)

**Archetypes (v1):** `record | table | message | doc | board | event | timeline | dashboard | file | form`. FE may implement a subset; unknown archetype ⇒ tier-3 fallback (never an error).

**SurfaceSpec (JSON Schema, prose form):**

```
spec_version   int, required, == 1
archetype      enum, required
source         { server: string, tool: string }, required
title_path     dot-path string, required
subtitle_path  dot-path, optional
fields[]       { label: string(1..40), path: dot-path, format?: enum(text|number|currency|datetime|badge|user) }  # record/message/doc
columns[]      { label, path, format?, align?: start|end }                                                        # table/board
items_path     dot-path to the array root                                                                          # table/board/timeline
group_by_path  dot-path                                                                                            # board lanes
link           { label: string, url_path: dot-path }, optional
```

Dot-paths are dots + array indices only (`a.b.0.c`) — no expressions, no functions, no code. **The schema has zero side-effectful members**: no handlers, no free-form URLs (only `url_path` into payload data, host-sanitized at render), no templates. This is the injection-blast-radius guarantee (plan D9).

**SurfaceEnvelope** (what rides inside event payloads under key `surface`):

```
surface_uri  string   "<archetype>://<server-slug>/<tool-or-resource>/<id>"
archetype    SurfaceArchetype
state        { spec?: SurfaceSpec, data: unknown }
diff?        { spec?: SurfaceSpec, changes: GenericFieldChange[]-compatible }
```

`state.spec` optional: absent ⇒ FE renders tier-3 generic; the spec may arrive later via `surface_spec_generated` and is merged by URI (PRD-04 projector behavior — do **not** implement the merge here).

## Acceptance criteria

1. `npm run typecheck --workspace @0x-copilot/api-types` green.
2. New pytest in ai-backend: pydantic model round-trips the 3 golden fixtures below; `validate_surface_spec` rejects (a) unknown archetype, (b) a path with `(` or `[a]` syntax, (c) missing `title_path` — with actionable messages.
3. **Parity test** (the load-bearing one): a pytest that loads `surface_spec.schema.json` and asserts the pydantic model's field set / enums / required lists match it (walk `model_json_schema()` vs the file). Drift fails CI.
4. 3 golden fixtures committed under `services/ai-backend/tests/unit/agent_runtime/surfaces/fixtures/`: `linear_get_issue.spec.json` (record), `github_list_issues.spec.json` (table), `gmail_message.spec.json` (message).
5. No behavior change anywhere: no emitter, no renderer, no registration.

## Non-goals / guardrails

- Do NOT touch `eventProjector.ts`, any renderer, `CallMcpTool`, or any host app.
- Do NOT add npm/pip dependencies without checking the lockfile first; prefer hand-rolled path validation over a jsonpath lib.
- Event-type addition must follow the established pattern exactly (see how `adapter_generated` / `draft_updated` were added: enum + projector branch + api-types payload + `RuntimeEventPayloadByType` entry).
