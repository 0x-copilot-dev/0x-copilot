# PR 05 — A1: User/Org/Role Schema Foundation

**Spec ID:** A1 | **Track:** Identity & Access | **Wave:** 2 (Auth Foundation) | **Estimated effort:** L
**Depends on:** C2 (migration tooling)
**Required for:** every Track A PR (A2–A10)

---

## 1. Functional Specification

### 1.1 Goal

Today the system has no concept of a `user` or `organization` row. Identity flows in via headers, trusted because of an HMAC-signed bearer token minted _outside_ the system. To support real login (any IdP), per-org configuration, MFA, SCIM, and audit-of-who-did-what, we need a real identity schema as the source of truth in `services/backend`.

This PR is **schema only — no behavior change.** Records, repositories, and migrations land. Subsequent PRs (A2 onwards) consume them.

### 1.2 User-visible behavior

- **End user:** none.
- **Developer:** new Pydantic record types and store interfaces become available for A2–A10 to use.
- **Operator:** new tables in the backend schema; no rows yet (until A2's bootstrap path or A3/A5's IdP path creates them).

### 1.3 Out of scope

- Any login flow.
- Any session minting.
- Any UI.
- Any IdP integration.

---

## 2. Technical Specification

### 2.1 Architecture

- All identity tables live in `services/backend` schema (the auth source of truth).
- Every table carries `org_id NOT NULL` (in single-tenant deploys, always equals the singleton org_id).
- Soft-delete via `deleted_at TIMESTAMPTZ NULL` + partial unique indexes excluding deleted rows.
- ID format: `org_<26-char-uuid>`, `usr_<26>`, `mem_<26>`, `role_<26>`, `asn_<26>`, `prv_<26>`, `evt_<26>`. (ulid-style; matches existing `mcp_<...>` style in the schema.)
- **`login_attempts` table is pulled forward into A1** (rather than A8) so A3–A7 can emit into it from day one.

### 2.2 Schema changes

Migration `services/backend/migrations/0004_identity_foundation.sql`:

```sql
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE organizations (
    org_id           TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    slug             TEXT NOT NULL,
    deployment_kind  TEXT NOT NULL CHECK (deployment_kind IN ('saas', 'single_tenant')),
    status           TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'deleted')),
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    deleted_at       TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_organizations_slug
    ON organizations (slug) WHERE deleted_at IS NULL;

CREATE TABLE users (
    user_id            TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL REFERENCES organizations(org_id),
    primary_email      CITEXT NOT NULL,
    email_verified_at  TIMESTAMPTZ,
    display_name       TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('active', 'disabled', 'pending_invite')),
    is_service_account BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at       TIMESTAMPTZ,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL,
    deleted_at         TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_users_org_email
    ON users (org_id, lower(primary_email)) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_org_status ON users (org_id, status);
CREATE INDEX idx_users_org_last_seen ON users (org_id, last_seen_at DESC);

CREATE TABLE organization_members (
    member_id           TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES organizations(org_id),
    user_id             TEXT NOT NULL REFERENCES users(user_id),
    joined_at           TIMESTAMPTZ NOT NULL,
    invited_by_user_id  TEXT,
    removed_at          TIMESTAMPTZ,
    source              TEXT NOT NULL CHECK (source IN ('local','oidc','saml','scim','bootstrap'))
);
CREATE UNIQUE INDEX idx_org_members_active
    ON organization_members (org_id, user_id) WHERE removed_at IS NULL;

CREATE TABLE roles (
    role_id             TEXT PRIMARY KEY,
    org_id              TEXT,                      -- NULL = system role
    name                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    is_system           BOOLEAN NOT NULL DEFAULT FALSE,
    permission_scopes   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    deleted_at          TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_roles_org_name
    ON roles (COALESCE(org_id, '<system>'), name) WHERE deleted_at IS NULL;

CREATE TABLE role_assignments (
    assignment_id        TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    role_id              TEXT NOT NULL REFERENCES roles(role_id),
    granted_by_user_id   TEXT,
    granted_at           TIMESTAMPTZ NOT NULL,
    revoked_at           TIMESTAMPTZ,
    reason               TEXT
);
CREATE UNIQUE INDEX idx_role_assignments_active
    ON role_assignments (org_id, user_id, role_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_role_assignments_role ON role_assignments (org_id, role_id);

CREATE TABLE auth_providers (
    provider_id              TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL,
    kind                     TEXT NOT NULL CHECK (kind IN ('local','oidc','saml','scim')),
    display_name             TEXT NOT NULL,
    enabled                  BOOLEAN NOT NULL DEFAULT TRUE,
    config                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    encrypted_client_secret  TEXT,                  -- via TokenVault
    created_at               TIMESTAMPTZ NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL,
    deleted_at               TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_auth_providers_unique
    ON auth_providers (org_id, kind, display_name) WHERE deleted_at IS NULL;
CREATE INDEX idx_auth_providers_enabled
    ON auth_providers (org_id, enabled) WHERE deleted_at IS NULL;

CREATE TABLE identity_audit_events (
    audit_id          TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    actor_user_id     TEXT,
    subject_user_id   TEXT,
    action            TEXT NOT NULL,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_ip        TEXT,
    user_agent        TEXT,
    created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_identity_audit_org_created
    ON identity_audit_events (org_id, created_at DESC);
CREATE INDEX idx_identity_audit_org_action
    ON identity_audit_events (org_id, action, created_at DESC);
CREATE INDEX idx_identity_audit_subject
    ON identity_audit_events (subject_user_id, created_at DESC);

-- login_attempts table pulled forward from A8 so A3-A7 emit into it from day one
CREATE TABLE login_attempts (
    attempt_id        TEXT PRIMARY KEY,
    org_id            TEXT,                          -- NULL when org unknown (e.g. unknown email)
    email_attempted   CITEXT,
    user_id           TEXT,
    auth_kind         TEXT NOT NULL CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key')),
    outcome           TEXT NOT NULL CHECK (outcome IN ('success','bad_password','unknown_user','locked_out','mfa_failed','provider_rejected')),
    ip                TEXT,
    user_agent        TEXT,
    failure_reason    TEXT,
    created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_login_attempts_org_email
    ON login_attempts (org_id, email_attempted, created_at DESC);
CREATE INDEX idx_login_attempts_ip
    ON login_attempts (ip, created_at DESC);
CREATE INDEX idx_login_attempts_user
    ON login_attempts (user_id, created_at DESC);
CREATE INDEX idx_login_attempts_created
    ON login_attempts (created_at);  -- for retention sweeps
```

Rollback drops all tables in reverse order; CITEXT extension stays.

### 2.3 Endpoints

None (foundation PR).

### 2.4 Code changes

**New module package** `services/backend/src/backend_app/identity/`:

- `__init__.py`
- `store.py` — `IdentityStore` interface, `PostgresIdentityStore`, `InMemoryIdentityStore`.

**Pydantic records** appended to [services/backend/src/backend_app/contracts.py](../../services/backend/src/backend_app/contracts.py):

- `OrganizationRecord`, `UserRecord`, `OrganizationMemberRecord`, `RoleRecord`, `RoleAssignmentRecord`, `AuthProviderRecord`, `IdentityAuditEventRecord`, `LoginAttemptRecord`.

**Seeded system roles** (inserted by the migration as a `0004b_seed_system_roles.sql`):

- `admin` — `["admin:users","admin:idp","admin:audit_export","skills:write","mcp:write"]`
- `employee` — `["runtime:use","skills:read","mcp:read"]`
- `auditor` — `["audit:read"]`
- `service` — `["runtime:use"]`

Permission scope strings come from C1's `packages/service-contracts/...` → A10 will later promote them into a typed enum.

### 2.5 Trust model & failure semantics

Pure schema. No runtime trust changes.

### 2.6 Tenant isolation

- Every non-system row carries `org_id`.
- All query patterns lead with `org_id` filter (enforced in repo layer; defense-in-depth comes with C5 RLS).
- Same email allowed across two orgs (different `(org_id, email)` keys).
- System roles (`org_id IS NULL`) are visible to all orgs but never shadow per-org roles.

### 2.7 Observability

- All write methods emit a structured log: `identity_write table=… org_id=… op=insert|update|delete`.
- An empty `identity_audit_events` table is normal at this point — A3+ populate it.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Migration 0004 applies cleanly on an empty DB.
- [ ] All seed system roles exist after migration (`SELECT count(*) FROM roles WHERE is_system=true` = 4).
- [ ] CRUD via repo works in both `InMemoryIdentityStore` and `PostgresIdentityStore`.
- [ ] Same email in two orgs creates two independent users.
- [ ] Soft-deleting a user lets a re-create with the same email succeed.
- [ ] No identity table can be inserted with `org_id IS NULL` except `roles` (where `is_system=true`).

### 3.2 Test plan

**Unit:**

- CRUD tests per record type for both adapters.
- Soft-delete + recreate test for `users`, `auth_providers`, `roles`.
- CITEXT case-insensitive lookup test (`Foo@x.com` matches `foo@X.COM`).
- Role assignment uniqueness: same user + same role + active twice → second insert fails.

**Integration:**

- Migration roundtrip: apply → rollback → apply again; schema identical.
- `pg_dump --schema-only` snapshot test.

**Tenant-isolation (mirrors [services/backend/tests/test_tenant_isolation_skills_mcp.py](../../services/backend/tests/test_tenant_isolation_skills_mcp.py)):**

- Insert two users with same email in two orgs; query each org returns only its own user.
- List users for org_a never returns org_b rows.

### 3.3 Compliance evidence produced

- Foundation for "audit logging completeness" (`identity_audit_events` table exists, append-only at repo layer).
- Foundation for "tenant isolation" (every table has org_id; tests cover cross-tenant).
- Foundation for "retention and deletion verification" (soft-delete strategy documented).

### 3.4 Rollout plan

Pure additive. No back-compat concerns; no existing rows. No env var. Tests required to pass before merge.

### 3.5 Backout plan

Migration rollback drops all new tables. No code paths depend on them yet.

### 3.6 Definition of done

- [ ] Migration 0004 applied with seed roles.
- [ ] Pydantic records + both store adapters land.
- [ ] All unit + integration + tenant-isolation tests pass.
- [ ] Spec doc cross-referenced from `docs/specs/auth/README.md`.

---

## 4. Critical files

- New: `services/backend/migrations/0004_identity_foundation.sql` (+ rollback, + seed file)
- Modify: [services/backend/src/backend_app/contracts.py](../../services/backend/src/backend_app/contracts.py) — append identity records.
- New: `services/backend/src/backend_app/identity/__init__.py`
- New: `services/backend/src/backend_app/identity/store.py`
- New: `services/backend/tests/identity/test_identity_store.py`
- New: `services/backend/tests/identity/test_tenant_isolation.py`
