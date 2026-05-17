# Phase 7.A: tier2-registry-backend

## Vision

Server-side storage + review queue + promotion lifecycle for agent-generated tier-2 adapters. Single source of truth for the `(scheme, version, source)` triple that other tenants will execute on their machines. Three primitives:

- **Candidate** — a locally-generated adapter on Tenant A that has met the §9.5.3 success criteria. Submitted by the desktop client (7B). Private to its origin tenant until promoted; reviewer queue (7C) only sees it post-submit.
- **Review** — one decision row per (`candidate_id`, `reviewer_id`) capturing `approve` / `reject` / `request-changes` + notes. Append-only.
- **Promoted** — a frozen copy of an approved candidate. Distinguished by `schema_version`. Visible to every tenant that has not opted out. Origin tenant is recorded for audit, not for access control.

Three load-bearing invariants:

- **Tenant isolation on read.** Candidate listing filters by the verified `org_id`; reviewers (`admin:audit_export` scope) see the global queue. Promoted-adapter listing respects the tenant opt-out flag.
- **Audit immutability.** Submit / review / promote / opt-out are recorded in the existing `audit_events` chain so backfill of a malicious row is detectable. The chain signs each append against its predecessor; no in-memory mutation is allowed.
- **Source storage is content-addressed.** Adapter source goes through a `SourceStorage` port — filesystem-backed in dev, S3-injectable in prod. The Postgres row carries the storage key + sha256 digest; the bytes themselves are external. Re-promote is idempotent because the digest pins the artifact.

The reviewer never sees real tenant data. The candidate carries `harvest_metrics` (zero-error sessions, anonymized) but no payload. Synthetic sample state belongs to the layout template — 7C generates it client-side from the scheme.

## Status

- Status: in-progress
- Agent slug: `tier2-registry-backend`
- Branch: `desktop/phase-7-tier2-registry-backend`
- Worktree: `.claude/worktrees/agent-af37fda056fc04231`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `services/backend/src/backend_app/adapter_registry/__init__.py`
- `services/backend/src/backend_app/adapter_registry/models.py`
- `services/backend/src/backend_app/adapter_registry/storage.py`
- `services/backend/src/backend_app/adapter_registry/store.py`
- `services/backend/src/backend_app/adapter_registry/registry_service.py`
- `services/backend/src/backend_app/adapter_registry/routes.py`
- `services/backend/src/backend_app/app.py` — wire `register_adapter_registry_routes(app, ...)`
- `services/backend/migrations/0031_adapter_registry.sql` (+ rollback)
- `services/backend/migrations/MANIFEST.lock` — append checksum
- `services/backend/tests/unit/adapter_registry/__init__.py`
- `services/backend/tests/unit/adapter_registry/test_models.py`
- `services/backend/tests/unit/adapter_registry/test_registry_service.py`
- `services/backend/tests/unit/adapter_registry/test_storage.py`
- `services/backend/tests/integration/api/__init__.py`
- `services/backend/tests/integration/api/test_adapter_registry_routes.py`
- `services/backend-facade/src/backend_facade/adapter_registry_routes.py`
- `services/backend-facade/src/backend_facade/app.py` — wire `register_adapter_registry_routes(app)`
- `services/backend-facade/tests/unit/__init__.py`
- `services/backend-facade/tests/unit/test_adapter_registry_routes.py`
- `packages/api-types/src/index.ts` — append `PromotedAdapter`, `AdapterCandidate`, `AdapterCandidateSubmission`, `AdapterReviewDecision`, `AdapterReviewAction`, list/opt-out response shapes.
- `docs/plan/desktop/phase-7/7A-tier2-registry-backend.md` — this file.

**Out of scope** (do NOT touch):

- `apps/desktop/main/adapters/{harvest,download,opt-out}.ts` — Phase 7B owns. We expose the contract; they consume it.
- `apps/frontend/src/admin/adapter-review/*` — Phase 7C owns.
- `services/ai-backend/**` — adapter generation (Phase 6B) emits the `adapter_generated` event the client harvests; we never call back into ai-backend.
- Frontend wiring of the new types; that lives in 7C.
- Visual regression / screenshot diff (post-Phase 7 per §9.5.1).

## Functional requirements

- [ ] FR-1: `POST /v1/adapter_registry/candidates` accepts an authenticated tenant member's submission `{scheme, version, layout, source, harvest_metrics}` and persists it as `status='submitted'` with `tenant_id` derived from the verified bearer.
- [ ] FR-2: `GET /v1/admin/adapter_registry/candidates?status=...` returns the global review queue. Admin-only (`ADMIN_AUDIT_EXPORT` scope — the closest existing admin-level scope; no new scope is introduced in this phase).
- [ ] FR-3: `POST /v1/admin/adapter_registry/candidates/{id}/decisions` records a review (`approve` / `reject` / `request-changes`). On `approve`, atomically: insert into `promoted_adapters` with `schema_version = max(existing) + 1` for the `(scheme)` row, append both `adapter_reviewed` and `adapter_promoted` audit events, mark candidate `status='approved'`. On `reject`: mark `status='rejected'`. On `request-changes`: mark `status='changes-requested'`.
- [ ] FR-4: `GET /v1/adapter_registry/promoted` returns the union of promoted adapters available to the caller's tenant: highest `schema_version` per `scheme`, excluding any if the tenant has opted out of shared adapters. Each row carries the `storage_key` + `source_digest` so the client can download from object storage.
- [ ] FR-5: `PUT /v1/adapter_registry/opt-out` toggles the tenant-level `opted_out` boolean; admin-only. Future `GET /promoted` calls honor.
- [ ] FR-6: Every state transition (submit / review / promote / opt-out) appends an `audit_events` row with action `adapter_candidate_submitted` / `adapter_reviewed` / `adapter_promoted` / `tenant_opt_out_changed`. Chain signing reuses the existing `_AuditChain` infrastructure so backfill produces a detectable hash break.
- [ ] FR-7: `SourceStorage` port: `put(key, bytes) -> digest`, `get(key) -> bytes`, `delete(key) -> bool`. `LocalFilesystemSourceStorage` for dev/tests writes under `{data_dir}/adapter_registry/{scheme}/{version}.js`. Production injects an S3-backed adapter via `create_app(..., adapter_source_storage=...)` (boto3 is NOT added as a runtime dep in this PR — a comment in `storage.py` notes the injection point).
- [ ] FR-8: Tenant-isolation negative tests: Tenant B cannot read Tenant A's `submitted` candidate via the admin queue absent admin scope; Tenant B cannot read Tenant A's promoted list under Tenant A's `org_id` (the route always rebinds `org_id` from the verified identity).
- [ ] FR-9: Audit immutability test: append two events, then attempt to mutate one in the in-memory store; the chain's next signature would no longer match — this is verified by re-signing and comparing.
- [ ] FR-10: Opt-out honored: a tenant whose `opted_out=true` row exists receives an empty `promoted` list even when promoted rows are present.

## Non-functional requirements

- **Python 3.13.** Pydantic v2 models, SQLAlchemy 2.0 patterns. No `Any` in request/response models.
- **Tenant scoping.** Every store method accepts `org_id` explicitly; the route layer derives `org_id` from `BackendServiceAuthenticator.scoped_identity` only.
- **Comments.** Per `CLAUDE.md` §6.1: default to no comments. Security-relevant invariants (e.g. "rebinds tenant from verified identity") are allowed in one line.
- **No bare `dict` / `Any` in route bodies** — every wire shape is a Pydantic model.
- **Migrations.** New tables + RLS policy + indices; rollback file mirrors. Manifest checksum appended.
- **Tests.** Unit (models, service, storage), integration (API routes), facade (proxy + auth gating). Cover the must-test items (tenant isolation, audit immutability, opt-out, admin-only gating).

## Interfaces consumed

- `BackendServiceAuthenticator.scoped_identity` (backend) and `FacadeAuthenticator.authenticate_request` (facade) — established session identity, never accept tenant ID as input.
- `RequireScopes(ADMIN_AUDIT_EXPORT)` for admin-only routes; `RequireScopes(RUNTIME_USE)` for tenant-member routes.
- `AuditEventRecord` + chain signing pattern from `backend_app.store._AuditChain`.

## Interfaces produced

Backend `/internal/v1/adapter_registry/...` (consumed by facade, not by apps):

```http
POST   /internal/v1/adapter_registry/candidates
GET    /internal/v1/adapter_registry/candidates
GET    /internal/v1/adapter_registry/candidates/{id}
POST   /internal/v1/adapter_registry/candidates/{id}/decisions
GET    /internal/v1/adapter_registry/promoted
PUT    /internal/v1/adapter_registry/opt-out
```

Facade `/v1/adapter_registry/...` (consumed by apps):

```http
POST   /v1/adapter_registry/candidates
GET    /v1/adapter_registry/promoted
PUT    /v1/adapter_registry/opt-out
GET    /v1/admin/adapter_registry/candidates
POST   /v1/admin/adapter_registry/candidates/{id}/decisions
```

api-types: `PromotedAdapter`, `AdapterCandidate`, `AdapterCandidateSubmission`, `AdapterReviewAction`, `AdapterReviewDecision`, `PromotedAdaptersResponse`, `AdapterCandidateListResponse`, `AdapterRegistryOptOutRequest`, `AdapterRegistryOptOutResponse`.

## Tests

Backend (`services/backend/tests/`):

- `unit/adapter_registry/test_models.py` — Pydantic validators (scheme format, status enum, action enum, version positivity).
- `unit/adapter_registry/test_storage.py` — `LocalFilesystemSourceStorage` round-trip + digest stability + delete.
- `unit/adapter_registry/test_registry_service.py` — submit / list / decide (approve+reject+request-changes) / list_promoted / opt-out, tenant isolation negatives, audit chain hash chain check.
- `integration/api/test_adapter_registry_routes.py` — backend HTTP routes: 401 without bearer, 403 on admin route without scope, happy-path submit → admin approve → tenant list promoted, opt-out honored, tenant-isolation negative on candidate read.

Facade (`services/backend-facade/tests/unit/`):

- `test_adapter_registry_routes.py` — proxy semantics: candidate submit forwards verified `org_id`; admin routes round-trip; opt-out PUT round-trips; auth required; payloads pass through.

## Open questions / parking lot

- The `harvest_metrics` blob is currently `dict[str, int]` (zero_error_sessions, total_sessions, etc.). If 7B needs richer shapes we widen later — the PRD pins "zero-error sessions" as the only required metric.
- `LocalFilesystemSourceStorage` uses `{data_dir}/adapter_registry/{scheme}/{version}.js`. Path-traversal is gated by validating `scheme` against `[A-Za-z0-9._:-]+` at the model layer; the storage layer asserts again.
- A second `admin:adapter_review` scope would be cleaner than reusing `ADMIN_AUDIT_EXPORT`. Deferred to Phase 8 — adding a new scope touches `packages/service-contracts` and all three services, which is out of this phase's blast radius.
