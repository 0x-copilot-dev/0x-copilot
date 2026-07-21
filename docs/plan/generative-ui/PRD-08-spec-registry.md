# PRD-08 — Spec persistence: backend registry + backend-http adapter (Wave 2, after PRD-07)

**Goal:** durable, org-scoped SurfaceSpec storage for the team/web deployment: internal endpoints on `services/backend` (beside the existing `adapter_registry`) and the `backend-http` implementation of `SurfaceSpecStorePort` in ai-backend. Desktop single-user keeps the file store; backend selection follows the store-backend env pattern.

**Depends on:** PRD-07 (port + key semantics frozen). **Scope:** `services/backend` + the ai-backend client adapter. Cross-service via HTTP only.

## Scope — files

| File | Change |
|---|---|
| `services/backend/src/backend_app/surface_specs/` (new module: `contracts.py`, `service.py`, `store.py`, `router.py`) | NEW — follow the module conventions of `adapter_registry/`. Record: `{key fields, spec: dict, origin: "generated"\|"curated-override", generator_model, skill_version, created_at, org_id, user_id}`. Endpoints (INTERNAL ONLY): `GET /internal/v1/surfaces/specs?server&tool&shape_hash&schema_version&skill_version`, `PUT /internal/v1/surfaces/specs`, `DELETE .../{id}` (admin/override path). Auth: same internal service-token + org/user header discipline as the other `/internal/v1/*` routes — caller identity headers are required and validated, never trusted from body |
| `services/backend` store wiring | EXTEND — persistence via the backend's existing storage layer (mirror how `adapter_registry` persists; in-memory fallback for tests) |
| `services/ai-backend/src/agent_runtime/capabilities/surfaces/backend_store.py` | NEW — `BackendHttpSurfaceSpecStore(SurfaceSpecStorePort)`: GET/PUT against the internal endpoints via the same internal-client conventions as `capabilities/mcp/backend_provider.py` (base URL + service token env). Read-through cache in-process (TTL 10 min) so render-path lookups never hammer HTTP |
| ai-backend store selection | EXTEND — `SURFACE_SPEC_STORE_BACKEND` env: `memory` (default test), `file` (desktop default), `backend` (team). Wire into the same composition point where PRD-07 injected the store |

## Behavior (normative)

- Specs are **org-scoped**: key + org_id uniqueness; no cross-org reads (test it).
- `PUT` upserts on the full key; `origin: curated-override` wins over `generated` on GET (the human-override path — an operator can pin a corrected spec without deploys).
- Validation on write: backend re-validates the spec against `surface_spec.schema.json` from `service-contracts` (both services already share that package via PYTHONPATH/build install — same mechanism as `adapter_allowlist`). Invalid ⇒ 422.
- Facade: **no app-facing routes in this PRD** (specs are runtime infrastructure; a Settings "views" surface is future work).

## Acceptance criteria

1. Backend unit: PUT→GET round-trip; org isolation (org B cannot read org A's spec); invalid spec 422; override precedence.
2. Internal-auth tests: missing service token ⇒ 401; missing org/user headers ⇒ 4xx per existing convention.
3. ai-backend unit: `BackendHttpSurfaceSpecStore` against a fake transport — hit, miss, TTL cache (second GET within TTL does not call HTTP), PUT after generation.
4. Env selection test: `SURFACE_SPEC_STORE_BACKEND=file|memory|backend` composes the right impl.
5. Both services' unit suites green; no cross-service imports (CI boundary check stays clean).

## Non-goals / guardrails

- No facade routes, no frontend, no admin UI.
- No global/community spec sharing (org-scoped only; marketplace is product P5).
- Do not modify the port Protocol from PRD-07 — if it doesn't fit, amend PRD-07 first.
