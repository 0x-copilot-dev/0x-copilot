# PR 08 — A4: Local Password Authentication and Bootstrap Admin

**Spec ID:** A4 | **Track:** Identity & Access | **Wave:** 3 (Parallel) | **Estimated effort:** M
**Depends on:** A1, A2
**Required for:** A8 (lockout integration), A9 (frontend)
**Parallel with:** A3 (OIDC), A5 (SAML), A7 (SCIM)

---

## 1. Functional Specification

### 1.1 Goal

Provide a local username+password login path. Two purposes:

1. **Bootstrap admin** for any deployment — the very first user must be able to log in before any IdP exists.
2. **Fallback IdP** for environments that allow it (small SaaS orgs, on-prem deploys not connected to corporate SSO yet).

Bank/gov deploys explicitly disable this path via `identity_policy.local_password_enabled=false`.

### 1.2 User-visible behavior

- **First-run operator:** sets `BOOTSTRAP_ADMIN_EMAIL=admin@…` env var → on first start, system creates one bootstrap admin and writes a one-time setup token to logs (operator copies it, uses it once to set the initial password).
- **End user (where enabled):** can log in with email + password; can reset via emailed link; can change password while logged in.
- **End user (where disabled):** the `/v1/auth/login` route returns 404 (not 401, to convey "this IdP isn't enabled").

### 1.3 Out of scope

- MFA enforcement (A6).
- Lockout / rate limiting (A8 — but emit `login_attempts` rows from day one).
- Email-sending infrastructure (this PR sets `password_reset_tokens.token_hash` and emits a `notify.password_reset_requested` event; an email worker subscribes to it — out of scope for THIS PR).
- Password rotation / mandatory-change-after-N-days (added when needed).

---

## 2. Technical Specification

### 2.1 Architecture

- **Hashing:** argon2id (RFC 9106), via `argon2-cffi`. Parameters tunable via env (`PASSWORD_ARGON2_MEMORY_KIB=65536`, `_TIME_COST=3`, `_PARALLELISM=2`); sane defaults match OWASP recommendations.
- **Pepper:** optional secret-pepper from env (`PASSWORD_PEPPER`); when set, prepended to plaintext before argon2.
- **Reset tokens:** opaque random string, length 32 bytes; only SHA-256 hash stored; consumed-once via `consumed_at`.
- **Bootstrap admin:** one-time. After first run, the bootstrap path is locked out via a sentinel row in `auth_providers.config` (or simply: refuse bootstrap if any admin user already exists).
- **Constant-time response** on unknown-email reset request — always 200, always emit identity_audit_event row.

### 2.2 Schema changes

Migration `services/backend/migrations/0007_local_password.sql`:

```sql
CREATE TABLE local_credentials (
    credential_id     TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    user_id           TEXT NOT NULL REFERENCES users(user_id),
    password_hash     TEXT NOT NULL,                 -- argon2id encoded incl. salt+params
    password_set_at   TIMESTAMPTZ NOT NULL,
    must_rotate_at    TIMESTAMPTZ,
    last_used_at      TIMESTAMPTZ,
    previous_hashes   JSONB NOT NULL DEFAULT '[]'::jsonb,    -- last N hashes for reuse window
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    deleted_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_local_credentials_user
    ON local_credentials (org_id, user_id) WHERE deleted_at IS NULL;

CREATE TABLE password_policies (
    policy_id          TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL UNIQUE,
    min_length         INTEGER NOT NULL DEFAULT 12,
    require_upper      BOOLEAN NOT NULL DEFAULT TRUE,
    require_lower      BOOLEAN NOT NULL DEFAULT TRUE,
    require_digit      BOOLEAN NOT NULL DEFAULT TRUE,
    require_symbol     BOOLEAN NOT NULL DEFAULT FALSE,
    rotation_days      INTEGER,                      -- NULL = no rotation requirement
    reuse_window       INTEGER NOT NULL DEFAULT 5,
    updated_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE password_reset_tokens (
    token_id     TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    token_hash   TEXT NOT NULL,                      -- sha256, never plaintext
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ,
    request_ip   TEXT
);
CREATE UNIQUE INDEX idx_password_reset_token_hash ON password_reset_tokens (token_hash);
CREATE INDEX idx_password_reset_user_pending
    ON password_reset_tokens (user_id, expires_at) WHERE consumed_at IS NULL;
CREATE INDEX idx_password_reset_expiring
    ON password_reset_tokens (expires_at) WHERE consumed_at IS NULL;
```

### 2.3 Endpoints

**Facade public:**

- `POST /v1/auth/login` body `{email, password, org_slug?}` → bearer token + session. `org_slug` only used in SaaS multi-tenant; ignored in single-tenant.
- `POST /v1/auth/password/reset/request` body `{email, org_slug?}` → always 200 (anti-enumeration). Emits notify event.
- `POST /v1/auth/password/reset/confirm` body `{token, new_password}` → 200 / 400.
- `POST /v1/auth/password/change` (auth required) body `{current, new}` → 200 / 400.

**Backend internal:**

- `POST /internal/v1/auth/local/verify` body `{org_id, email, password}` → `{user_id, requires_password_change}`.
- `POST /internal/v1/auth/local/bootstrap-admin` (one-time, env-gated) body `{email, setup_token}` → `{user_id}`.
- `POST /internal/v1/auth/password/{request,confirm,change}` mirroring facade routes.

### 2.4 Code changes

**New:**

- `services/backend/src/backend_app/identity/passwords.py` — hash, verify, policy-enforce, reuse check.
- `services/backend/src/backend_app/identity/bootstrap.py` — first-run admin path.

**Modify:**

- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) — register routes.
- [services/backend-facade/src/backend_facade/auth_routes.py](../../services/backend-facade/src/backend_facade/auth_routes.py) — add login + reset endpoints.
- [services/backend/requirements.txt](../../services/backend/requirements.txt) — add `argon2-cffi`.

**Constant-time guard** on unknown email: even when no user exists, run a dummy argon2 verify against a cached hash so timing leaks no info.

### 2.5 Trust model & failure semantics

- `local_password_enabled` toggle from `identity_policy` (resolved per org); when false, route returns 404.
- Failed login → audit row + login_attempts row + 401.
- Successful login → mint session via A2 internal API.
- Reset token: single-use; consumed atomically; expired → 400.
- Bootstrap-admin endpoint: requires both `BOOTSTRAP_ADMIN_EMAIL` env AND a one-time `BOOTSTRAP_SETUP_TOKEN` env match; refuses if any admin user already exists.

### 2.6 Tenant isolation

- Same email in two orgs → distinct credentials.
- Login route requires either `org_slug` (SaaS) or single-tenant singleton org_id.
- Cross-tenant negative test: login with org_a email + org_b's slug → 401.

### 2.7 Observability

- Audit actions: `password.set`, `password.changed`, `password.reset_requested`, `password.reset_confirmed`, `password.bootstrap_admin_created`.
- Login attempts: every login (success/fail) writes a `login_attempts` row.
- Metrics: `password_login_total{outcome}`, `password_reset_requested_total`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Bootstrap admin path: empty DB + `BOOTSTRAP_ADMIN_EMAIL=…` + setup token → admin can log in once and is forced to change password.
- [ ] Setting `identity_policy.local_password_enabled=false` for an org → `/v1/auth/login` for that org returns 404.
- [ ] Argon2id verify with correct password → 200; with wrong password → 401.
- [ ] Weak password rejected per policy; specific reason returned.
- [ ] Reset token consumed once; second use → 400.
- [ ] Reset request for unknown email → 200 (no enumeration), audit row recorded.
- [ ] Bootstrap path refused after any admin user exists.

### 3.2 Test plan

**Unit:**

- argon2id verify-doesn't-rehash-on-success.
- Weak password rejected per each policy attribute.
- Reset token: single-use, expired-rejected.
- Reuse window blocks the same hash.
- Constant-time response on unknown email (timing variance < 5ms across 1000 trials).
- Bootstrap refused when admin exists.

**Integration:**

- Bootstrap → first login → forced change → subsequent login.
- Cross-tenant: login with right password but wrong org_slug → 401.
- Disabled IdP returns 404.

### 3.3 Compliance evidence produced

- argon2id (NIST SP 800-63B AAL1).
- Reset-token hashing at rest.
- Audit of password change/reset.
- Bootstrap admin separation: env + one-time token, single-use.

### 3.4 Rollout plan

- Off by default in SaaS for new orgs (`local_password_enabled=false`).
- On by default in single-tenant for the bootstrap admin only.
- Per-org admin can flip the toggle.

### 3.5 Backout plan

Set `local_password_enabled=false`. Existing sessions continue.

### 3.6 Definition of done

- [ ] Migration 0007 applied.
- [ ] argon2id wired with sane defaults.
- [ ] Bootstrap path tested on empty DB.
- [ ] Disabled-IdP path returns 404.
- [ ] All unit + integration tests pass.

---

## 4. Critical files

- New: `services/backend/migrations/0007_local_password.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/passwords.py`
- New: `services/backend/src/backend_app/identity/bootstrap.py`
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- Modify: `services/backend-facade/src/backend_facade/auth_routes.py`
- Modify: `services/backend/requirements.txt` — add `argon2-cffi`.
