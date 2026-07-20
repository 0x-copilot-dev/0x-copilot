# Service-boundary note — account-merge runtime leg

New cross-service surface introduced by the account-merge saga (PRD §6.4).

## The surface

`POST /internal/v1/admin/account-merge` on **ai-backend**.

- **Caller**: `services/backend` only (`HttpRuntimeMergeClient` in
  `backend_app/identity/account_merge.py`), during the saga's
  `backend_done → runtime_done` step. The facade never exposes it.
- **Auth**: shared `ENTERPRISE_SERVICE_TOKEN` (`x-enterprise-service-token`);
  NOT tenant-scoped — the explicit absorbed/survivor pairs travel in the
  body because the operation intentionally spans two tenants.
- **Discovery**: `AI_BACKEND_URL` in the backend's environment (the desktop
  supervisor and `run-local.mjs` export it). Without it the backend wires
  `UnconfiguredRuntimeMergeClient`, which fails CLOSED at the saga's runtime
  checkpoint — a merge never silently skips the ai-backend re-key.

## Contract

Request:

```json
{
  "merge_id": "amg_…",
  "absorbed_org_id": "org_…",
  "absorbed_user_id": "usr_…",
  "survivor_org_id": "org_…",
  "survivor_user_id": "usr_…"
}
```

Response: `{ "merge_id", "status": "completed"|"noop", "tables": {…}, "warnings": […] }`.
Idempotent — a re-run after completion returns `noop` with zero counts.

Types live in `services/ai-backend/src/runtime_api/schemas/account_merge.py`;
the backend client hand-builds the request dict. The PRD (§6.4) preferred a
shared typed contract in `packages/api-types`/`service-contracts` — deferred
because api-types is the APP-facing contract package and service-contracts is
constants-only; promoting an internal service-to-service shape there would
widen both packages' charters. Revisit if a second internal surface appears.

## Boundary rules honored

- `services/backend` does NOT import ai-backend code — HTTP only.
- The endpoint runs on the ai-backend worker-role cross-tenant connection
  (same trust path as the SIEM export); RLS-enforced deployments must grant
  it BYPASSRLS (deployment control, PRD §7).
- The file-native (desktop JSONL) store returns **501** — fail-closed until a
  re-key for that layout exists.
