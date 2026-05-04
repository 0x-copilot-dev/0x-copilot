-- Stage 5 backout for C5 (backend) — disables Row-Level Security on every
-- tenant-scoped backend table. Use only as an incident-response hot patch.

ALTER TABLE mcp_servers           DISABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_auth_sessions     DISABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_auth_connections  DISABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_audit_events      DISABLE ROW LEVEL SECURITY;
ALTER TABLE skills                DISABLE ROW LEVEL SECURITY;
ALTER TABLE skill_audit_events    DISABLE ROW LEVEL SECURITY;
ALTER TABLE organizations         DISABLE ROW LEVEL SECURITY;
ALTER TABLE users                 DISABLE ROW LEVEL SECURITY;
ALTER TABLE organization_members  DISABLE ROW LEVEL SECURITY;
ALTER TABLE role_assignments      DISABLE ROW LEVEL SECURITY;
ALTER TABLE auth_providers        DISABLE ROW LEVEL SECURITY;
ALTER TABLE identity_audit_events DISABLE ROW LEVEL SECURITY;
ALTER TABLE identity_policies     DISABLE ROW LEVEL SECURITY;
ALTER TABLE sessions              DISABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_authentications  DISABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_identities       DISABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_refresh_tokens   DISABLE ROW LEVEL SECURITY;
ALTER TABLE local_credentials     DISABLE ROW LEVEL SECURITY;
ALTER TABLE password_policies     DISABLE ROW LEVEL SECURITY;
ALTER TABLE password_reset_tokens DISABLE ROW LEVEL SECURITY;
ALTER TABLE roles                 DISABLE ROW LEVEL SECURITY;
ALTER TABLE login_attempts        DISABLE ROW LEVEL SECURITY;
