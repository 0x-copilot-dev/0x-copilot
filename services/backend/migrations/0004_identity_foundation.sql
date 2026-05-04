-- A1: User / org / role / auth-provider schema foundation.
--
-- Schema-only migration. No code path consumes these tables yet — A2
-- introduces sessions, A3..A8 introduce IdP-specific state, and A10 wires
-- RBAC. See docs/roadmap/05-a1-user-org-schema.md for the full spec.
--
-- All tables carry org_id (system roles in `roles` are the documented
-- exception; see CHECK constraint there). Soft-delete via deleted_at + a
-- partial unique index excluding deleted rows so a re-create succeeds.

CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS organizations (
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_slug
    ON organizations (slug) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS users (
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_org_email
    ON users (org_id, lower(primary_email)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_users_org_status
    ON users (org_id, status);
CREATE INDEX IF NOT EXISTS idx_users_org_last_seen
    ON users (org_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS organization_members (
    member_id           TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES organizations(org_id),
    user_id             TEXT NOT NULL REFERENCES users(user_id),
    joined_at           TIMESTAMPTZ NOT NULL,
    invited_by_user_id  TEXT,
    removed_at          TIMESTAMPTZ,
    source              TEXT NOT NULL CHECK (source IN ('local','oidc','saml','scim','bootstrap'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_members_active
    ON organization_members (org_id, user_id) WHERE removed_at IS NULL;

-- roles allows org_id NULL specifically for system roles. A CHECK enforces
-- that the only rows missing org_id are flagged is_system=true so a buggy
-- INSERT cannot create an "orphan" role accessible to every tenant.
CREATE TABLE IF NOT EXISTS roles (
    role_id             TEXT PRIMARY KEY,
    org_id              TEXT,
    name                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    is_system           BOOLEAN NOT NULL DEFAULT FALSE,
    permission_scopes   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    deleted_at          TIMESTAMPTZ,
    CONSTRAINT roles_system_or_org CHECK (
        (is_system = TRUE  AND org_id IS NULL) OR
        (is_system = FALSE AND org_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_org_name
    ON roles (COALESCE(org_id, '<system>'), name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_roles_system
    ON roles (is_system) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS role_assignments (
    assignment_id        TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    role_id              TEXT NOT NULL REFERENCES roles(role_id),
    granted_by_user_id   TEXT,
    granted_at           TIMESTAMPTZ NOT NULL,
    revoked_at           TIMESTAMPTZ,
    reason               TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_role_assignments_active
    ON role_assignments (org_id, user_id, role_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_role_assignments_role
    ON role_assignments (org_id, role_id);

CREATE TABLE IF NOT EXISTS auth_providers (
    provider_id              TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL,
    kind                     TEXT NOT NULL CHECK (kind IN ('local','oidc','saml','scim')),
    display_name             TEXT NOT NULL,
    enabled                  BOOLEAN NOT NULL DEFAULT TRUE,
    config                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    encrypted_client_secret  TEXT,
    created_at               TIMESTAMPTZ NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL,
    deleted_at               TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_providers_unique
    ON auth_providers (org_id, kind, display_name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_auth_providers_enabled
    ON auth_providers (org_id, enabled) WHERE deleted_at IS NULL;

-- identity_audit_events is append-only at the repo layer (no UPDATE / DELETE
-- methods). Phase 2 of the audit-hardening migration (PR C-something) can
-- add chain signatures + an immutable trigger on the same shape used in
-- mcp_audit_events / skill_audit_events.
CREATE TABLE IF NOT EXISTS identity_audit_events (
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
CREATE INDEX IF NOT EXISTS idx_identity_audit_org_created
    ON identity_audit_events (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_audit_org_action
    ON identity_audit_events (org_id, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_audit_subject
    ON identity_audit_events (subject_user_id, created_at DESC);

-- login_attempts is pulled forward from A8 so A3..A7 emit into it from day
-- one. NULLable org_id and user_id because failures can occur before we
-- even know who the caller claims to be (e.g. random bot hitting /v1/auth/login
-- with nonexistent emails).
CREATE TABLE IF NOT EXISTS login_attempts (
    attempt_id        TEXT PRIMARY KEY,
    org_id            TEXT,
    email_attempted   CITEXT,
    user_id           TEXT,
    auth_kind         TEXT NOT NULL CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key')),
    outcome           TEXT NOT NULL CHECK (outcome IN ('success','bad_password','unknown_user','locked_out','mfa_failed','provider_rejected')),
    ip                TEXT,
    user_agent        TEXT,
    failure_reason    TEXT,
    created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_org_email
    ON login_attempts (org_id, email_attempted, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip
    ON login_attempts (ip, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user
    ON login_attempts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_created
    ON login_attempts (created_at);
