-- C5: Row-Level Security policies and dedicated app/admin roles for backend.
--
-- Mirrors services/ai-backend/migrations/0008_rls_tenant_isolation.sql. The
-- backend service hosts MCP, identity, and session state, so the table list
-- is different but the rollout sequence is identical:
--
--   1. apply this migration (policies + grants exist but dormant).
--   2. ship adapter code that sets ``app.current_org_id`` on every
--      connection checkout (Stage 2 in docs/roadmap/15-c5-rls-tenant-isolation.md).
--   3. apply staged/do_rls.sql (Stage 3) once Stage 2 is verified.
--
-- Roles are created idempotently so this can run on a shared cluster where
-- ai-backend already created them.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        CREATE ROLE enterprise_app NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_admin') THEN
        CREATE ROLE enterprise_admin BYPASSRLS NOINHERIT;
    END IF;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    mcp_servers,
    mcp_auth_sessions,
    mcp_auth_connections,
    mcp_audit_events,
    skills,
    skill_audit_events,
    organizations,
    users,
    organization_members,
    role_assignments,
    auth_providers,
    identity_audit_events,
    identity_policies,
    sessions,
    oidc_authentications,
    oidc_identities,
    oidc_refresh_tokens,
    local_credentials,
    password_policies,
    password_reset_tokens
TO enterprise_app;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO enterprise_app;

-- ``roles`` and ``login_attempts`` carry rows whose ``org_id`` is intentionally
-- NULL (system roles, pre-auth login attempts). Their tenant policy ORs in
-- the NULL case so a tenant session still sees system-shared rows but cannot
-- read another tenant's overrides.
GRANT SELECT, INSERT, UPDATE, DELETE ON roles, login_attempts TO enterprise_app;

-- ``oidc_jwks_cache`` is a global JWKS cache, no org scoping.
GRANT SELECT, INSERT, UPDATE, DELETE ON oidc_jwks_cache TO enterprise_app;

-- Tenant-isolation policies. Dormant until ENABLE ROW LEVEL SECURITY runs
-- (see staged/do_rls.sql).

CREATE POLICY tenant_isolation ON mcp_servers
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON mcp_auth_sessions
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON mcp_auth_connections
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON mcp_audit_events
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON skills
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON skill_audit_events
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- ``organizations`` keys ``org_id`` as the primary key — the policy still
-- compares against ``app.current_org_id`` so a tenant session sees only its
-- own org row.
CREATE POLICY tenant_isolation ON organizations
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON users
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON organization_members
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON role_assignments
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON auth_providers
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON identity_audit_events
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON identity_policies
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON sessions
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON oidc_authentications
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON oidc_identities
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON oidc_refresh_tokens
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON local_credentials
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON password_policies
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON password_reset_tokens
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- ``roles`` has nullable ``org_id`` — system-defined roles (org_id IS NULL)
-- are visible to every tenant session for read; tenant overrides are scoped.
CREATE POLICY tenant_isolation_or_system ON roles
    USING (
        org_id IS NULL
        OR org_id = current_setting('app.current_org_id', true)
    )
    WITH CHECK (
        org_id = current_setting('app.current_org_id', true)
    );

-- ``login_attempts`` has nullable ``org_id`` for pre-auth attempts where the
-- caller failed before user lookup. Allow inserts unconditionally so the
-- audit trail isn't lost; reads stay tenant-scoped (with NULL fallback for
-- the auth service to inspect anonymous attempts via the worker role).
CREATE POLICY tenant_isolation_or_anon ON login_attempts
    USING (
        current_setting('app.role', true) = 'auth'
        OR org_id IS NULL
        OR org_id = current_setting('app.current_org_id', true)
    )
    WITH CHECK (TRUE);
