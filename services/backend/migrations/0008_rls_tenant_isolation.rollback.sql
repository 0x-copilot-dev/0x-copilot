-- Rollback for 0008_rls_tenant_isolation.sql.
--
-- Drops policies first (safe regardless of whether ENABLE ROW LEVEL SECURITY
-- has been applied via staged/do_rls.sql), then revokes grants. Roles are
-- intentionally NOT dropped — other databases on the same cluster may
-- depend on them.

DROP POLICY IF EXISTS tenant_isolation ON mcp_servers;
DROP POLICY IF EXISTS tenant_isolation ON mcp_auth_sessions;
DROP POLICY IF EXISTS tenant_isolation ON mcp_auth_connections;
DROP POLICY IF EXISTS tenant_isolation ON mcp_audit_events;
DROP POLICY IF EXISTS tenant_isolation ON skills;
DROP POLICY IF EXISTS tenant_isolation ON skill_audit_events;
DROP POLICY IF EXISTS tenant_isolation ON organizations;
DROP POLICY IF EXISTS tenant_isolation ON users;
DROP POLICY IF EXISTS tenant_isolation ON organization_members;
DROP POLICY IF EXISTS tenant_isolation ON role_assignments;
DROP POLICY IF EXISTS tenant_isolation ON auth_providers;
DROP POLICY IF EXISTS tenant_isolation ON identity_audit_events;
DROP POLICY IF EXISTS tenant_isolation ON identity_policies;
DROP POLICY IF EXISTS tenant_isolation ON sessions;
DROP POLICY IF EXISTS tenant_isolation ON oidc_authentications;
DROP POLICY IF EXISTS tenant_isolation ON oidc_identities;
DROP POLICY IF EXISTS tenant_isolation ON oidc_refresh_tokens;
DROP POLICY IF EXISTS tenant_isolation ON local_credentials;
DROP POLICY IF EXISTS tenant_isolation ON password_policies;
DROP POLICY IF EXISTS tenant_isolation ON password_reset_tokens;
DROP POLICY IF EXISTS tenant_isolation_or_system ON roles;
DROP POLICY IF EXISTS tenant_isolation_or_anon ON login_attempts;

REVOKE SELECT, INSERT, UPDATE, DELETE ON
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
    password_reset_tokens,
    roles,
    login_attempts,
    oidc_jwks_cache
FROM enterprise_app;
