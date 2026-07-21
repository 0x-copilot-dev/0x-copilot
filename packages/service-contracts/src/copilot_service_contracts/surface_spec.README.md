# SurfaceSpec Schema — Single Source of Truth

`surface_spec.schema.json` is the canonical specification of a **SurfaceSpec**:
a small, schema-validated JSON document that binds a connector tool's output
shape onto a generic archetype renderer's slots. It is the frozen cross-PRD
interface for the generative-UI effort (plan D2). Both runtimes derive from it:

- **AI backend** —
  `services/ai-backend/src/agent_runtime/capabilities/surfaces/spec_models.py`
  loads the pydantic mirror and validates raw specs against the field/enum/
  required rules declared here (via
  `copilot_service_contracts.surface_spec.load_surface_spec_schema`).
- **Frontend / api-types** — `packages/api-types/src/index.ts` mirrors the
  types (`SurfaceSpec`, `SurfaceArchetype`, `SurfaceEnvelope`) and the runtime
  guards (`isSurfaceSpec`, `isSurfaceEnvelope`).

A cross-language parity test (`test_schema_parity.py`) asserts the pydantic
model's field set / enums / required lists match this file. If they drift, CI
fails — the schema and the model cannot disagree silently.

## The contract

```jsonc
{
  "spec_version": 1, // required, frozen == 1
  "archetype": "record", // required, one of the archetype enum
  "source": {
    // required
    "server": "seed:linear",
    "tool": "get_issue",
  },
  "title_path": "issue.title", // required — dot-path to the headline
  "subtitle_path": "issue.identifier", // optional
  "fields": [
    // record | message | doc
    { "label": "State", "path": "issue.state.name", "format": "badge" },
  ],
  "columns": [
    // table | board
    { "label": "Title", "path": "title", "align": "start" },
  ],
  "items_path": "issues", // table | board | timeline — array root
  "group_by_path": "state", // board lanes
  "link": {
    // optional
    "label": "Open in Linear",
    "url_path": "issue.url",
  },
}
```

### Archetypes (v1)

`record | table | message | doc | board | event | timeline | dashboard | file | form`.

A frontend may implement a subset. An **unknown archetype is not an error** —
it falls back to the tier-3 generic renderer.

### Dot-paths

Every `*_path` is a dotted accessor: identifier segments and array indices only
(`a.b.0.c`). No expressions, no functions, no brackets, no code.

### The zero-side-effect guarantee (plan D9)

The schema has **no side-effectful members**: no handlers, no free-form URLs
(only `url_path` fields that resolve into payload data and are host-sanitised at
render), no templates. Tool output is untrusted; the worst case an injected
value can achieve is mislabeled display text, rendered inert as an escaped React
text node. This structural property — not prompt hygiene — is the injection
blast-radius bound.

## Versioning policy

`spec_version` is frozen at `1`. Bumping it, adding a top-level field, removing
an enum value, or adding a required field is a **contract change** and must go
through an amendment to `PRD-01-surface-contract.md`, not a local edit. When you
change this file:

1. Update the pydantic mirror (`spec_models.py`) in the same PR.
2. Update the TypeScript mirror (`packages/api-types/src/index.ts`).
3. The parity test enforces (1); keep it green.

Optional additions the schema already tolerates (a new `format` value, a new
optional field) are non-breaking but still travel through all three sites.

## What is NOT here

- The archetype renderers (live in `packages/surface-renderers`).
- The spec generator + spec-authoring skill (live in
  `services/ai-backend/.../capabilities/surfaces/`).
- The `surface_spec_generated` event projection (lives in
  `services/ai-backend/.../runtime_api/schemas/events.py`).

This file is a schema only.
