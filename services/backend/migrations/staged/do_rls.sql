-- Stage 3 of C5 (backend) — turns on Row-Level Security for every tenant-
-- scoped backend table. Applied separately from yoyo migrations.
--
-- See services/ai-backend/migrations/staged/do_rls.sql for the rollout
-- procedure; the same psql invocation pattern applies.

ALTER TABLE mcp_servers           ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_auth_sessions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_auth_connections  ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_audit_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE skills                ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_audit_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE users                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_members  ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_assignments      ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_providers        ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_policies     ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions              ENABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_authentications  ENABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_identities       ENABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_refresh_tokens   ENABLE ROW LEVEL SECURITY;
ALTER TABLE local_credentials     ENABLE ROW LEVEL SECURITY;
ALTER TABLE password_policies     ENABLE ROW LEVEL SECURITY;
ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE login_attempts        ENABLE ROW LEVEL SECURITY;

ALTER TABLE mcp_servers           FORCE ROW LEVEL SECURITY;
ALTER TABLE mcp_auth_sessions     FORCE ROW LEVEL SECURITY;
ALTER TABLE mcp_auth_connections  FORCE ROW LEVEL SECURITY;
ALTER TABLE mcp_audit_events      FORCE ROW LEVEL SECURITY;
ALTER TABLE skills                FORCE ROW LEVEL SECURITY;
ALTER TABLE skill_audit_events    FORCE ROW LEVEL SECURITY;
ALTER TABLE organizations         FORCE ROW LEVEL SECURITY;
ALTER TABLE users                 FORCE ROW LEVEL SECURITY;
ALTER TABLE organization_members  FORCE ROW LEVEL SECURITY;
ALTER TABLE role_assignments      FORCE ROW LEVEL SECURITY;
ALTER TABLE auth_providers        FORCE ROW LEVEL SECURITY;
ALTER TABLE identity_audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE identity_policies     FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions              FORCE ROW LEVEL SECURITY;
ALTER TABLE oidc_authentications  FORCE ROW LEVEL SECURITY;
ALTER TABLE oidc_identities       FORCE ROW LEVEL SECURITY;
ALTER TABLE oidc_refresh_tokens   FORCE ROW LEVEL SECURITY;
ALTER TABLE local_credentials     FORCE ROW LEVEL SECURITY;
ALTER TABLE password_policies     FORCE ROW LEVEL SECURITY;
ALTER TABLE password_reset_tokens FORCE ROW LEVEL SECURITY;
ALTER TABLE roles                 FORCE ROW LEVEL SECURITY;
ALTER TABLE login_attempts        FORCE ROW LEVEL SECURITY;
