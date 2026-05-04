# A7 — SCIM 2.0 user/group provisioning (implementation contract)

Roadmap source: [docs/roadmap/10-a7-scim.md](../../../../../docs/roadmap/10-a7-scim.md).
Implementation deltas only — what we shipped and where it diverges from
the roadmap text.

## Migration

- File: `services/backend/migrations/0015_scim.sql` (+ rollback).
- The roadmap names this `0009`; that number was already taken in this
  branch by RLS-related migration sequencing. Numbering is monotonic.
- RLS deferred (matches `0014_saml.sql`, `0011_mfa.sql`, etc.) — added
  in a follow-up sweep along with the rest of the post-RLS tables.

## Tables

```
scim_tokens
  token_id PK, org_id, provider_id REFERENCES auth_providers,
  token_hash UNIQUE (sha256), token_prefix (first 8 chars for display),
  created_by_user_id, created_at, expires_at, revoked_at, last_used_at

scim_external_ids
  mapping_id PK, org_id, user_id (NULLABLE), group_id (NULLABLE),
  provider_id, external_id, created_at
  CHECK ((user_id IS NOT NULL) <> (group_id IS NOT NULL))
  UNIQUE (provider_id, external_id)

scim_groups
  group_id PK, org_id, display_name, external_id, mapped_role_id,
  created_at, updated_at, deleted_at
  UNIQUE (org_id, display_name) WHERE deleted_at IS NULL

scim_group_members
  membership_id PK, org_id, group_id, user_id,
  added_at, removed_at
  UNIQUE (group_id, user_id) WHERE removed_at IS NULL

ALTER TABLE users ADD COLUMN scim_external_id TEXT;
CREATE UNIQUE INDEX idx_users_scim_external_id
  ON users (org_id, scim_external_id) WHERE scim_external_id IS NOT NULL;
```

## Token shape & rotation

- `secrets.token_urlsafe(32)` plaintext, returned ONCE at mint.
- Server stores `sha256(plaintext)` as `token_hash` and the first 8 chars
  of the plaintext as `token_prefix` (so an admin listing can tell
  tokens apart without ever seeing the secret again — matches GitHub PAT).
- Mint never auto-revokes the previous token: zero-downtime rotation
  requires both old and new tokens to be valid simultaneously.

## Filter parser

Hand-rolled, no library dep. Supported subset (the 99% case for IdPs):

- `userName eq "x@y.com"`
- `active eq true`
- `userName eq "x" and active eq true`
- `userName pr` (presence)

Anything else → 400 with `scimType=invalidFilter`. Extending the parser
for `co` / `sw` / `or` / `not` is a small, additive future PR.

## Endpoints

**Facade public** (mounted at `/scim/v2/*`, NOT under `/v1`):

- `GET /scim/v2/Users?filter=&startIndex=&count=`
- `POST /scim/v2/Users`
- `GET /scim/v2/Users/{id}`
- `PUT /scim/v2/Users/{id}`
- `PATCH /scim/v2/Users/{id}` (JSON-Patch ops: replace, add, remove)
- `DELETE /scim/v2/Users/{id}`
- `GET /scim/v2/Groups`, `POST /scim/v2/Groups`,
  `GET/PUT/PATCH/DELETE /scim/v2/Groups/{id}`
- `GET /scim/v2/ServiceProviderConfig`
- `GET /scim/v2/Schemas`
- `GET /scim/v2/ResourceTypes`

**Backend internal** mirrors with `/internal/v1/auth/scim/{provider_id}/*`,
plus token mint / list / revoke:

- `POST /internal/v1/auth/scim/{provider_id}/tokens` — mint (returns
  plaintext once)
- `GET  /internal/v1/auth/scim/{provider_id}/tokens` — list (prefix only)
- `DELETE /internal/v1/auth/scim/{provider_id}/tokens/{token_id}` — revoke

## Trust model

- Bearer-token validated by `sha256(presented) ==` lookup against
  `scim_tokens.token_hash`. Revoked or expired → 401.
- All queries scope by the org_id resolved from the token row — token A
  (org_1) calling `/Users` returns only org_1's users, even when the
  caller specifies `?filter=...` matching org_2's data.
- `scim_required` policy (`identity_policies.scim_required`): when on,
  local password (A4) returns 404 and OIDC (A3) JIT provisioning is
  rejected with `user not provisioned via SCIM`. Bank/gov mode.

## Soft-delete semantics

- `PATCH .../Users/{id}` `{op: replace, path: active, value: false}` →
  sets `users.deleted_at` (matches the existing soft-delete column).
- `value: true` reactivates by clearing `deleted_at`.
- `DELETE` is also accepted but rare — IdPs prefer the active-flip.

## Group → role sync

When a `scim_groups.mapped_role_id` is set:

- `POST .../Groups/{id}/members` (add) → assigns the mapped role.
- Member removal → revokes the role assignment (sets `revoked_at`).
- Group deletion → revokes the role for all members.

If `mapped_role_id` is `NULL` the group still tracks membership but no
role assignments are created.

## Out of scope (per roadmap §1.3)

- SCIM `bulk` endpoint.
- Cross-org tenancy in SCIM.
- Custom SCIM extensions beyond `EnterpriseUser`.

## Tests

- `tests/identity/test_scim_filter.py` — parser unit tests.
- `tests/identity/test_scim_store.py` — storage CRUD + tenant isolation.
- `tests/identity/test_scim_service.py` — User/Group CRUD via the
  service, JSON-Patch op application, soft-delete, role sync, token
  mint/revoke, scim_required mode interaction.
- `tests/identity/test_scim_routes.py` — backend internal endpoint
  wire-level error mapping.
- `services/backend-facade/tests/test_scim_facade.py` — public surface
  smoke (Schemas / ResourceTypes / ServiceProviderConfig + bearer
  validation forwarding).
