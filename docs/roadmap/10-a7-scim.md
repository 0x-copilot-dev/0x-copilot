# PR 10 — A7: SCIM 2.0 User/Group Provisioning

**Spec ID:** A7 | **Track:** Identity & Access | **Wave:** 3 (Parallel) | **Estimated effort:** L
**Depends on:** A1, A2
**Required for:** A8 (login_attempts integration), A10 (RBAC role sync)
**Parallel with:** A3, A4, A5

---

## 1. Functional Specification

### 1.1 Goal

Add SCIM 2.0 (System for Cross-domain Identity Management) endpoints so enterprise IdPs (Okta, OneLogin, Azure AD, etc.) can provision and deprovision users + groups in our system automatically. Required for any bank/gov customer with > ~50 users — manual user management is unacceptable at that scale.

### 1.2 User-visible behavior

- **IdP admin:** configures our SCIM endpoint URL + per-org bearer token in their IdP. Users created/updated/deactivated in the IdP propagate within minutes.
- **End user (created via SCIM):** can log in via the linked SSO provider; cannot log in via local password (no `local_credentials` row).
- **Operator:** can mint and rotate SCIM tokens per-org; tokens shown once at creation (like a GitHub PAT).
- **Bank deploy mode:** when `identity_policy.scim_required=true`, local password is disabled AND OIDC JIT provisioning is rejected — only SCIM-provisioned users can exist.

### 1.3 Out of scope

- SCIM bulk endpoint (rarely used by IdPs).
- Cross-org tenancy in SCIM.
- Custom SCIM extensions beyond `EnterpriseUser`.

---

## 2. Technical Specification

### 2.1 Architecture

- Routed via facade for boundary consistency: `POST /scim/v2/*` on facade → backend `/internal/v1/auth/scim/{provider_id}/*` after token validation.
- Per-org SCIM token: backend mints, returns plaintext once, stores SHA-256 hash. Token includes a 4-byte prefix the operator can use to identify the token in lists.
- Soft-delete on user via `active=false` PATCH → sets `users.deleted_at`.
- Group → role mapping via `scim_groups.mapped_role_id`; group membership changes sync `role_assignments`.
- SCIM filter parser: small, hand-rolled or `scim2-filter-parser`; supports `eq`, `and`, `pr` (the 99% case for IdPs).

### 2.2 Schema changes

Migration `services/backend/migrations/0009_scim.sql`:

```sql
CREATE TABLE scim_tokens (
    token_id            TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    provider_id         TEXT NOT NULL REFERENCES auth_providers(provider_id),
    token_hash          TEXT NOT NULL,                -- sha256
    token_prefix        TEXT NOT NULL,                -- first 8 chars for display
    created_by_user_id  TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    last_used_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_scim_token_hash ON scim_tokens (token_hash);
CREATE INDEX idx_scim_token_org ON scim_tokens (org_id, revoked_at);

CREATE TABLE scim_external_ids (
    mapping_id   TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    user_id      TEXT REFERENCES users(user_id),
    group_id     TEXT,                                 -- references scim_groups
    provider_id  TEXT NOT NULL REFERENCES auth_providers(provider_id),
    external_id  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    CHECK ((user_id IS NOT NULL) <> (group_id IS NOT NULL))
);
CREATE UNIQUE INDEX idx_scim_external_id
    ON scim_external_ids (provider_id, external_id);
CREATE INDEX idx_scim_external_user ON scim_external_ids (user_id);
CREATE INDEX idx_scim_external_group ON scim_external_ids (group_id);

CREATE TABLE scim_groups (
    group_id        TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    external_id     TEXT,
    mapped_role_id  TEXT REFERENCES roles(role_id),
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    deleted_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_scim_groups_name
    ON scim_groups (org_id, display_name) WHERE deleted_at IS NULL;

CREATE TABLE scim_group_members (
    membership_id  TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    group_id       TEXT NOT NULL REFERENCES scim_groups(group_id),
    user_id        TEXT NOT NULL REFERENCES users(user_id),
    added_at       TIMESTAMPTZ NOT NULL,
    removed_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_scim_group_member_active
    ON scim_group_members (group_id, user_id) WHERE removed_at IS NULL;

ALTER TABLE users ADD COLUMN scim_external_id TEXT;
CREATE UNIQUE INDEX idx_users_scim_external_id
    ON users (org_id, scim_external_id) WHERE scim_external_id IS NOT NULL;
```

### 2.3 Endpoints

**Facade public (mounted at `/scim/v2/*`, NOT under `/v1`):**

- `GET /scim/v2/Users?filter=&startIndex=&count=`
- `POST /scim/v2/Users`
- `GET /scim/v2/Users/{id}`
- `PUT /scim/v2/Users/{id}`
- `PATCH /scim/v2/Users/{id}` (JSON-Patch)
- `DELETE /scim/v2/Users/{id}` (rare; most IdPs use `active=false`)
- `GET /scim/v2/Groups?filter=&startIndex=&count=`
- `POST /scim/v2/Groups`
- `PUT /scim/v2/Groups/{id}`, `PATCH`, `DELETE`
- `GET /scim/v2/ServiceProviderConfig`
- `GET /scim/v2/Schemas`
- `GET /scim/v2/ResourceTypes`

**Backend internal:**

- `POST /internal/v1/auth/scim/{provider_id}/users` (etc., mirroring above)
- `POST /internal/v1/auth/scim/{provider_id}/tokens` — mint
- `GET /internal/v1/auth/scim/{provider_id}/tokens` — list (prefix only)
- `DELETE /internal/v1/auth/scim/{provider_id}/tokens/{token_id}` — revoke

### 2.4 Code changes

**New:**

- `services/backend/src/backend_app/identity/scim.py` — handler logic, attribute mapping, group sync.
- `services/backend/src/backend_app/identity/scim_filter.py` — minimal filter parser (`eq`, `and`, `pr`).
- `services/backend-facade/src/backend_facade/scim_routes.py` — public surface, validates SCIM bearer, looks up `(provider_id, org_id)` from token, forwards as service-to-service.

**Modify:**

- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)

### 2.5 Trust model & failure semantics

- Bearer token validated by hash lookup against `scim_tokens`. Revoked or expired → 401.
- Token rotation: admin mints new token; old token continues to work until explicit revoke (no auto-revoke on mint, to allow zero-downtime rotation).
- All attribute writes go through validated PUT/PATCH; unknown attributes silently dropped (per SCIM RFC).
- Email collision: same email already exists in another `users` row → 409 with `scimType=uniqueness`.

### 2.6 Tenant isolation

- Token A (org_1's provider) can never list/create/update users in org_2. Test required.
- All queries scope by the `org_id` resolved from the token.

### 2.7 Observability

- Audit actions: `scim.token.minted`, `scim.token.revoked`, `scim.user.created`, `scim.user.updated`, `scim.user.deactivated`, `scim.group.created`, `scim.group.updated`, `scim.group.member_added`, `scim.group.member_removed`.
- Metrics: `scim_request_total{op,outcome}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Mint SCIM token for org_a → token shown once → subsequent SCIM requests with that token succeed.
- [ ] Token shown once; can be looked up later only by prefix.
- [ ] `POST /scim/v2/Users` creates a `users` row + `scim_external_ids` mapping.
- [ ] `PATCH /scim/v2/Users/{id}` with `{"Operations":[{"op":"replace","path":"active","value":false}]}` soft-deletes the user (`users.deleted_at` set).
- [ ] `PATCH` setting `active=true` reactivates.
- [ ] Group create + add member → `role_assignments` updated when `mapped_role_id` set.
- [ ] Filter `userName eq "x@y.com"` returns the right row.
- [ ] Token A (org_1) listing org_2 users returns `[]`.

### 3.2 Test plan

**Unit:**

- Filter parser handles `eq`, `and`, `pr`; rejects unsupported operators with 400.
- JSON-Patch operations apply correctly.
- ListResponse pagination (`startIndex`, `count`).
- Soft-delete via `active=false`.
- Reactivation.

**Integration:**

- Round-trip with a fake Okta SCIM client.
- `scim_required=true` → local login returns 404, OIDC JIT user-provision rejected.

**Tenant-isolation:**

- Token from org_a cannot read/write org_b users (verified at the token-resolution layer).

### 3.3 Compliance evidence produced

- SCIM 2.0 conformance (RFC 7643/7644).
- Token hashed at rest; shown once.
- Per-org isolation.
- Audit of every provision/deprovision (auto-deprovision when `active=false`).

### 3.4 Rollout plan

- SCIM provider added per-org by admin.
- Bank-mode toggle: `scim_required=true` per `identity_policy`.

### 3.5 Backout plan

Revoke all SCIM tokens for the org → IdP sync stops.

### 3.6 Definition of done

- [ ] Migration 0009 applied.
- [ ] All SCIM endpoints + filters tested.
- [ ] Round-trip with fake Okta client passes.
- [ ] Bank-mode integration test passes.
- [ ] Token rotation (mint new, revoke old) tested with no IdP downtime.

---

## 4. Critical files

- New: `services/backend/migrations/0009_scim.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/scim.py`
- New: `services/backend/src/backend_app/identity/scim_filter.py`
- New: `services/backend-facade/src/backend_facade/scim_routes.py`
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
