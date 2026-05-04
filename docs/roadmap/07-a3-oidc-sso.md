# PR 07 — A3: OIDC SSO (Google + Generic)

**Spec ID:** A3 | **Track:** Identity & Access | **Wave:** 3 (Parallel) | **Estimated effort:** L
**Depends on:** A1 (user/org schema), A2 (sessions), C2 (migrations)
**Required for:** A6 (MFA), A8 (lockout audit), A9 (frontend login)
**Parallel with:** A5 (SAML), A7 (SCIM)

---

## 1. Functional Specification

### 1.1 Goal

Add OpenID Connect (Authorization Code + PKCE) login to the product. Ships Google and a generic OIDC adapter (works with Okta, Azure AD, Auth0, Authentik, Keycloak). This is the SaaS-first login path; bank/gov deploys typically prefer SAML (A5) or SCIM-only (A7).

### 1.2 User-visible behavior

- **End user:** clicks "Sign in with Google" → IdP consent screen → returns logged in.
- **Org admin:** can configure an OIDC provider (issuer URL, client_id, client_secret, scopes) per-org via internal admin CLI (UI deferred).
- **Operator:** sees `oidc_authentications` rows for state machine, `oidc_identities` rows linking IdP `sub` to local users.

### 1.3 Out of scope

- Frontend login UI (A9).
- Group-claim → role _enforcement_ (this PR records mapping and assigns role; enforcement is A10).
- SAML (A5).
- MFA bound to provider claims (A6).

---

## 2. Technical Specification

### 2.1 Architecture

- **Reuse PKCE patterns** from [services/backend/src/backend_app/mcp_oauth.py](../../services/backend/src/backend_app/mcp_oauth.py). Extract a shared `_pkce.py` helper used by both MCP OAuth and OIDC.
- **Discovery:** fetch `<issuer>/.well-known/openid-configuration` once per provider, cache for `oidc.discovery_ttl_seconds` (default 1h).
- **JWKS:** fetch `<issuer>/jwks_uri`, cache in `oidc_jwks_cache` table with `expires_at` (default 1h, refreshed early on `kid` miss).
- **State + nonce** stored in `oidc_authentications` (consumed-once via `consumed_at`).
- **JIT provisioning** gated by `auth_providers.config.auto_provision_user`. When false, login fails for unknown subjects.
- **Refresh tokens** stored encrypted via TokenVault.

### 2.2 Schema changes

Migration `services/backend/migrations/0006_oidc.sql`:

```sql
CREATE TABLE oidc_authentications (
    auth_id        TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    provider_id    TEXT NOT NULL REFERENCES auth_providers(provider_id),
    state          TEXT NOT NULL,
    nonce          TEXT NOT NULL,
    code_verifier  TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    requested_at   TIMESTAMPTZ NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    ip             TEXT,
    user_agent     TEXT
);
CREATE UNIQUE INDEX idx_oidc_auth_state ON oidc_authentications (state);
CREATE INDEX idx_oidc_auth_pending ON oidc_authentications (expires_at) WHERE consumed_at IS NULL;

CREATE TABLE oidc_identities (
    identity_id      TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL,
    user_id          TEXT NOT NULL REFERENCES users(user_id),
    provider_id      TEXT NOT NULL REFERENCES auth_providers(provider_id),
    subject          TEXT NOT NULL,                 -- the IdP `sub` claim
    email_at_link    TEXT,
    linked_at        TIMESTAMPTZ NOT NULL,
    unlinked_at      TIMESTAMPTZ,
    claims_snapshot  JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX idx_oidc_identity_subject
    ON oidc_identities (provider_id, subject) WHERE unlinked_at IS NULL;
CREATE INDEX idx_oidc_identity_user
    ON oidc_identities (user_id) WHERE unlinked_at IS NULL;

CREATE TABLE oidc_refresh_tokens (
    token_id                 TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL,
    user_id                  TEXT NOT NULL REFERENCES users(user_id),
    provider_id              TEXT NOT NULL REFERENCES auth_providers(provider_id),
    encrypted_refresh_token  TEXT NOT NULL,         -- via TokenVault
    scope                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    expires_at               TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL,
    revoked_at               TIMESTAMPTZ
);
CREATE INDEX idx_oidc_refresh_active
    ON oidc_refresh_tokens (org_id, user_id, provider_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_oidc_refresh_expiring
    ON oidc_refresh_tokens (expires_at) WHERE revoked_at IS NULL;

CREATE TABLE oidc_jwks_cache (
    cache_id     TEXT PRIMARY KEY,
    provider_id  TEXT NOT NULL REFERENCES auth_providers(provider_id),
    jwks         JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_oidc_jwks_provider ON oidc_jwks_cache (provider_id, expires_at);
```

### 2.3 Endpoints

**Facade public:**

- `GET /v1/auth/oidc/{provider_id}/start?return_to=<url>` → 302 to IdP authorization endpoint OR returns `{auth_url, state}` JSON for native apps. Adds `oidc_authentications` row.
- `GET /v1/auth/oidc/callback?state=&code=&error=&error_description=` → exchanges code, mints session, sets/returns bearer.
- `GET /v1/auth/providers?org_slug=` → list of enabled IdP providers for the org's login screen.

**Backend internal:**

- `POST /internal/v1/auth/oidc/{provider_id}/authorize` → `{auth_url, state, expires_at}`.
- `POST /internal/v1/auth/oidc/callback` body: `{state, code}` → `{user_id, session_id, bearer_token}`.
- `GET /internal/v1/auth/oidc/providers?org_id=` — used by the facade public listing endpoint above.

### 2.4 Code changes

**New:**

- `services/backend/src/backend_app/identity/oidc.py` — state machine: authorize, callback, refresh.
- `services/backend/src/backend_app/identity/_pkce.py` — extracted shared helper (S256 challenge, state/nonce generation, PKCE pair).
- `services/backend/src/backend_app/identity/jwks.py` — JWKS fetch + cache + signature verification.

**Modify:**

- [services/backend/src/backend_app/mcp_oauth.py](../../services/backend/src/backend_app/mcp_oauth.py) — import from `_pkce.py` instead of inlining; preserve all existing behavior.
- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) — register internal OIDC routes.
- [services/backend-facade/src/backend_facade/auth_routes.py](../../services/backend-facade/src/backend_facade/auth_routes.py) (from A2) — add public OIDC routes.
- [services/backend/requirements.txt](../../services/backend/requirements.txt) — add `pyjwt[crypto]` and `cryptography` (already present likely).

**Group → role mapping** lives in `auth_providers.config`:

```json
{
  "issuer": "https://accounts.google.com",
  "client_id": "...",
  "scopes": ["openid", "email", "profile"],
  "auto_provision_user": true,
  "group_claim": "groups",
  "group_role_map": { "engineering": "employee", "admins": "admin" }
}
```

On every login, role assignments synced (add new, revoke missing) inside one transaction with the session create.

### 2.5 Trust model & failure semantics

- ID-token validation: signature against JWKS (rotation-aware via `kid`), `iss` matches provider config, `aud` matches client_id, `exp`/`nbf`/`iat` within 60s clock-skew leeway, `nonce` matches the auth row.
- State mismatch / consumed state / expired state → 400 with safe error.
- Unknown `sub` + `auto_provision_user=false` → 401, audit row.
- JWKS unavailable → reject login with safe error; don't fail-open with cached-but-stale-too-long JWKS.
- Refresh-token rotation: store new refresh-token, set old `revoked_at`.

### 2.6 Tenant isolation

- Provider config lives per-org; org_a's Google config can't authenticate into org_b.
- Login flow scoped to a specific `(org_id, provider_id)`; subject lookups filter by `provider_id` only (so users with the same Google account in two orgs get two local users — by design).

### 2.7 Observability

- Audit actions: `oidc.authorize_started`, `oidc.callback_succeeded`, `oidc.callback_failed{reason}`, `oidc.user_provisioned`, `oidc.role_synced`, `oidc.refresh_rotated`.
- Login attempts: every callback, success or failure, writes a `login_attempts` row (table from A1).
- Metrics: `oidc_login_total{provider_id_hash, outcome}`, `oidc_jwks_fetch_total`, `oidc_token_refresh_total`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Configure Google OIDC for a test org → `/v1/auth/oidc/{id}/start` returns a valid Google auth URL.
- [ ] Successful callback creates a `users` row (when `auto_provision_user=true`) + `oidc_identities` row + session.
- [ ] Second login on same `sub` reuses the user.
- [ ] Group claims resolve to role assignments; removed group → role revoked on next login.
- [ ] Replay of consumed `state` → 400 with audit row.
- [ ] Forged ID token (bad signature) → 401 with audit row.

### 3.2 Test plan

**Unit:**

- `test_state_mismatch_rejected`
- `test_nonce_mismatch_rejected`
- `test_expired_id_token_rejected`
- `test_aud_mismatch_rejected`
- `test_clock_skew_leeway_honored`
- `test_jwks_kid_rotation`
- `test_pkce_helper_shared_between_mcp_and_oidc` — both use the same module.

**Integration (with in-process fake OIDC IdP fixture):**

- Full callback round-trip mints a session.
- `auto_provision_user=true` creates user.
- `auto_provision_user=false` + unknown subject → 401, no user created.
- Refresh-token rotation: server returns new RT; old marked revoked.

**Tenant-isolation:**

- Provider in org_a; callback can't be redirected to authenticate as org_b user.

### 3.3 Compliance evidence produced

- Signed-token verification (CLAUDE.md §"caller-supplied identity untrusted unless from IdP claim").
- Refresh-token encryption-at-rest (TokenVault).
- Per-org IdP isolation.
- Audit on every authenticate.

### 3.4 Rollout plan

- New `auth_providers.kind='oidc'` rows added per-org by admin CLI.
- Feature gated by `identity_policy.enabled_idp_providers`.
- Frontend (A9) wires the provider list into the login screen.

### 3.5 Backout plan

Disable provider via `auth_providers.enabled=false`. Existing sessions continue to work.

### 3.6 Definition of done

- [ ] Migration 0006 applied.
- [ ] Google OIDC tested end-to-end against real Google.
- [ ] Generic OIDC tested against an in-process fake IdP.
- [ ] PKCE helper extracted and reused by mcp_oauth.py without behavior change.
- [ ] All audit + login-attempt rows produced.

---

## 4. Critical files

- New: `services/backend/migrations/0006_oidc.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/oidc.py`
- New: `services/backend/src/backend_app/identity/_pkce.py`
- New: `services/backend/src/backend_app/identity/jwks.py`
- Modify: [services/backend/src/backend_app/mcp_oauth.py](../../services/backend/src/backend_app/mcp_oauth.py) — import from `_pkce.py`.
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- Modify: `services/backend-facade/src/backend_facade/auth_routes.py` (from A2)
- Modify: `services/backend/requirements.txt` — add `pyjwt[crypto]`.
