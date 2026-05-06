# PR 8.3 — Settings Phase 3 (WebAuthn · Workspace API keys · Avatar pipeline · MFA enforcement)

## Context

PR 8.2 ([pr-8.2-settings-phase-2.md](pr-8.2-settings-phase-2.md)) closed Bio + Avatar (data-URL) + TOTP MFA + the API-keys IA split. Four follow-ups remain. Reassessed:

| Item                         | Backend ready?                                                                                                  | Verdict                                                                                                         |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **WebAuthn UI ceremony**     | Yes — `MfaService.webauthn_register_options` + `webauthn_register_finish`, internal routes already exist.       | **Implement.** FE ceremony + public wrappers.                                                                   |
| **Workspace API keys**       | Almost — `api_keys` table exists, missing only a `kind` column (`personal` / `workspace`).                      | **Implement.** One column + admin routes.                                                                       |
| **S3-style avatar pipeline** | No object storage. But the contract `avatar_url TEXT` already supports server-hosted URLs.                      | **Implement** as a Postgres-backed `AvatarStore` with a clean port. Same external contract as a future S3 swap. |
| **Per-IdP MFA enforcement**  | Yes — `identity_policies.mfa_required` already exists, already gates login. Just needs an admin editor surface. | **Implement.** GET/PUT + small Settings panel.                                                                  |

## Design principles

- **No new infra.** WebAuthn is a browser API (`navigator.credentials.create`), not a service. Workspace API keys reuse the existing table + store. Avatar pipeline uses Postgres `BYTEA` (same trade-off as `user_avatars` carrying a 50 KB blob — well within row-size headroom; concrete cap of 200 KB enforced server-side). MFA enforcement reuses the existing `identity_policies` row.
- **Adapters as ports.** The avatar `BYTEA` adapter sits behind an `AvatarStore` Protocol so a future PR can drop in an S3 / GCS adapter without touching routes or the FE.
- **Server is the source of truth.** Avatar bytes leave the FE once; the column stores `/v1/me/avatar/{user_id}?v={updated_at}` and every browser fetches the same URL. The previous `data:` URL pathway stays in the schema for back-compat (legacy values render as before).
- **DRY at every layer.** Each new public route is 5–10 lines because handlers delegate to existing services. New api-types reuse the existing `MfaFactor*` shapes; new mfa public routes reuse the same `_forward_me` proxy pattern.

## Streaming + agent harness sanity check

This PR is settings + identity. The agent harness (`runtime_worker` / `runtime_api`) is untouched. No new `RuntimeEventEnvelope` variants. The only new runtime touch-points are:

- `user_avatars` table (read-only from the avatar route; never enters agent context).
- `api_keys.kind` column (filter only; bearer auth path unchanged).
- `identity_policies.mfa_required` column (already gates login; this PR adds the admin editor).

## Scope

### A. WebAuthn UI ceremony

**Backend** — extend `me_mfa.py` with caller-scoped wrappers for the existing internal routes:

- `POST /internal/v1/me/mfa/factors/webauthn/register/start` → calls `service.webauthn_register_options`. Body: `{display_name, rp_id, rp_name, user_name, user_display_name}`. Identity from query.
- `POST /internal/v1/me/mfa/factors/webauthn/register/finish` → calls `service.webauthn_register_finish`. Body: `{factor_id, challenge_id, rp_id, expected_origin, attestation}`.

**Facade** — two more `_forward_me` proxies under `/v1/me/mfa/factors/webauthn/register/{start,finish}`.

**api-types** — `MfaWebAuthnStartRequestBody`, `MfaWebAuthnStartResponse`, `MfaWebAuthnFinishRequestBody`. The internal types (`WebAuthnRegisterStartResult`) carry org/user_id; the public types only carry rp_id/rp_name/expected_origin.

**Frontend** — `MfaPanel.tsx` gains a "Use a security key" button alongside the TOTP enroll. The flow:

1. POST start → response has `{factor_id, challenge_id, options}` where `options` is a `PublicKeyCredentialCreationOptions` JSON.
2. Decode the base64-url fields (`challenge`, `user.id`) into `Uint8Array`.
3. `navigator.credentials.create({ publicKey: options })`.
4. Encode the attestation `clientDataJSON` + `attestationObject` back to base64-url.
5. POST finish → 200 enables the factor.

The decoder helpers live in [`webauthnCodec.ts`](apps/frontend/src/features/settings/sections/webauthnCodec.ts) (new, ~30 lines) — pure functions, easy to unit-test.

### B. Workspace API keys

**Database** — `services/backend/migrations/0027_api_keys_kind.sql`:

```sql
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'personal'
    CHECK (kind IN ('personal', 'workspace'));
CREATE INDEX IF NOT EXISTS idx_api_keys_workspace
    ON api_keys (org_id) WHERE revoked_at IS NULL AND kind = 'workspace';
```

**Backend store** — `ApiKeyRow.kind: Literal["personal", "workspace"]` (default `personal`); `list_for_user` filters by kind; new `list_for_workspace(org_id, *, include_revoked)`. Postgres adapter SELECT/INSERT include the column.

**Backend routes** — extend `api_keys.py`:

- `GET    /internal/v1/workspace/api-keys` (admin scope) — list workspace-issued.
- `POST   /internal/v1/workspace/api-keys` (admin scope) — create with `kind='workspace'`. The token is still owned by the calling admin user (audit attribution); the `kind` flag distinguishes scope.
- `DELETE /internal/v1/workspace/api-keys/{id}` (admin scope).
- `POST   /internal/v1/workspace/api-keys/{id}/rotate` (admin scope).

**Facade** — public `/v1/workspace/api-keys/*` mirrors. Reuses the existing `_proxy` pattern; no new helper.

**api-types** — extend `ApiKeySummary` with `kind: 'personal' | 'workspace'`. New `WorkspaceApiKeyListResponse` (just `{keys: ApiKeySummary[]}`).

**Frontend** — `ApiKeys.tsx`'s Workspace tab now renders a real CRUD UI (mirrors the Personal body with admin guard). Replaces the empty state introduced in PR 8.2.

### C. Server-side avatar pipeline (Postgres-backed)

**Database** — `services/backend/migrations/0028_user_avatars.sql`:

```sql
CREATE TABLE IF NOT EXISTS user_avatars (
  user_id      TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
  org_id       TEXT NOT NULL REFERENCES organizations(org_id),
  content_type TEXT NOT NULL CHECK (content_type IN ('image/png','image/jpeg','image/webp')),
  bytes        BYTEA NOT NULL,
  size_bytes   INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 204800),  -- ≤ 200 KB
  etag         TEXT NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE user_avatars ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_avatars
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
```

**Backend** — `backend_app/identity/avatar_store.py` defines `AvatarStore` (Protocol) + `InMemoryAvatarStore` + `PostgresAvatarStore`. Each adapter handles `get`, `upsert(user_id, org_id, content_type, bytes)`, `delete`. Future cloud adapter is a third subclass; the route layer doesn't change.

**Backend routes** — `backend_app/routes/me_avatar.py`:

- `POST   /internal/v1/me/avatar` — multipart `file=`; validates content-type (allowlist) + size (≤200 KB). On write, updates `user_profiles.avatar_url` to `/v1/me/avatar/{user_id}?v={updated_at_epoch}`.
- `GET    /internal/v1/me/avatar/{user_id}` — service-token gated; returns `Response(content=bytes, media_type=content_type, headers={ETag, Cache-Control: 'private, max-age=86400'})`.
- `DELETE /internal/v1/me/avatar` — clears the row + nulls `avatar_url`.

**Facade** — `/v1/me/avatar` (POST/DELETE) forwarded with multipart preserved (httpx `files=` for upload, body stream for GET). `GET /v1/me/avatar/{user_id}` proxies the bytes verbatim.

**Frontend** — replace the data-URL pipeline:

- `avatarPipeline.ts` resizes to 256×256 `Blob` (not data URL) and returns the Blob + content-type.
- New `uploadAvatar(blob)` in `meApi.ts` does a multipart POST.
- `Profile.tsx`'s file picker uses the Blob upload, then refreshes the profile to pick up the new `avatar_url`.
- The "Use a URL instead" disclosure stays — admins can still paste an external CDN URL.
- Legacy `data:` URLs already in profiles render unchanged (server still tolerates them; the validator allows the existing pattern).

### D. Per-IdP MFA enforcement editor

**Backend** — extend `routes/identity_policies.py` (or new `routes/me_mfa_policy.py`):

- `GET    /internal/v1/workspace/mfa-policy` (admin scope) — `{ mfa_required: bool, step_up_window_seconds: int | null }`.
- `PUT    /internal/v1/workspace/mfa-policy` (admin scope) — RFC 7396 merge-patch. Audit on change.

The store layer already exists (`IdentityStore.get_identity_policy` / `upsert_identity_policy`); the new routes are 10–15 lines each.

**Facade** — `/v1/workspace/mfa-policy` GET + PUT. Uses the existing `_forward_policy` helper variant — just a new slug.

**api-types** — `WorkspaceMfaPolicy`, `UpdateWorkspaceMfaPolicyRequest`.

**Frontend** — new section in `WorkspaceSettings.tsx` (or a sibling `WorkspaceMfaSettings.tsx` rendered by the existing rail row). Toggle for "Require MFA for sign-in" + a select for the step-up window (15 min / 1 h / 8 h). Read-only for non-admins.

## Files touched

**New**

- `services/backend/migrations/0027_api_keys_kind.sql` (+ rollback)
- `services/backend/migrations/0028_user_avatars.sql` (+ rollback)
- `services/backend/src/backend_app/identity/avatar_store.py`
- `services/backend/src/backend_app/routes/me_avatar.py`
- `services/backend/src/backend_app/routes/workspace_api_keys.py`
- `services/backend/src/backend_app/routes/workspace_mfa_policy.py`
- `apps/frontend/src/features/settings/sections/webauthnCodec.ts`
- `apps/frontend/src/features/settings/sections/WorkspaceMfaSettings.tsx`
- `apps/frontend/src/api/avatarApi.ts`

**Modified**

- `services/backend/src/backend_app/api_keys/store.py` — `kind` column, list_for_workspace, list_for_user filter.
- `services/backend/src/backend_app/routes/api_keys.py` — pass-through `kind=personal` on the `/internal/v1/me/api-keys` path.
- `services/backend/src/backend_app/routes/me_mfa.py` — WebAuthn start/finish wrappers.
- `services/backend/src/backend_app/routes/me_profile.py` — drop the `data:` URL allow-list now that we have a real pipeline (kept tolerant of remote URLs and existing data: rows).
- `services/backend/src/backend_app/app.py` — wire new routes.
- `services/backend-facade/src/backend_facade/me_routes.py` — proxies for WebAuthn, avatar (multipart-aware), workspace api-keys, mfa-policy.
- `apps/frontend/src/features/settings/sections/MfaPanel.tsx` — WebAuthn flow.
- `apps/frontend/src/features/settings/sections/Profile.tsx` — multipart upload path.
- `apps/frontend/src/features/settings/sections/avatarPipeline.ts` — return `Blob` + content-type.
- `apps/frontend/src/features/settings/sections/ApiKeys.tsx` — wire workspace tab to real data.
- `apps/frontend/src/api/meApi.ts` + `mfaApi.ts` — new functions.
- `apps/frontend/src/features/settings/SettingsScreen.tsx` — render `WorkspaceMfaSettings` inside the existing `workspace` section (or as a sibling section under WORKSPACE).
- `packages/api-types/src/index.ts` — `WebAuthn*` body shapes, `ApiKeySummary.kind`, `WorkspaceMfaPolicy*`.

## Verification

```bash
npm run typecheck --workspace @enterprise-search/api-types
npm run typecheck --workspace @enterprise-search/frontend
cd services/backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest tests/test_me_profile_preferences.py tests/test_api_keys.py tests/test_mfa_routes.py -q
```

Manual:

- WebAuthn enroll on a hardware key (or virtual authenticator in DevTools) → factor lists with kind=webauthn, sign-in re-uses existing MFA challenge flow.
- Workspace API keys: admin creates one → bearer auth path still works (existing flow, just `kind` filter).
- Avatar upload: 1 MB JPEG resizes client-side, uploads to `/v1/me/avatar`, `<img src=…>` shows the new value; reloading other browsers picks up the cache-busted URL.
- MFA enforcement: admin toggles on → next non-MFA login is rejected with `mfa:pending` until enrollment.

## Out of scope (Phase 4+)

- Multi-tenant MFA policy (per-IdP, not per-org).
- Avatar versioning history.
- API key fine-grained scope picker (currently inherits caller's scopes).
- WebAuthn sign-in (already supported by backend; this PR only adds enrollment UI — sign-in ceremony lives behind `MfaPrompt.tsx`).
