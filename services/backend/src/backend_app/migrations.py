"""PostgreSQL schema for the backend-owned MCP registry tables."""

POSTGRES_MCP_REGISTRY_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
  server_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  url TEXT NOT NULL,
  transport TEXT NOT NULL,
  auth_mode TEXT NOT NULL,
  auth_state TEXT NOT NULL,
  health TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  required_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
  last_discovery JSONB NOT NULL DEFAULT '{}'::jsonb,
  oauth_client JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_scope
  ON mcp_servers (org_id, user_id, enabled);

CREATE TABLE IF NOT EXISTS mcp_auth_sessions (
  session_id TEXT PRIMARY KEY,
  server_id TEXT NOT NULL REFERENCES mcp_servers(server_id) ON DELETE CASCADE,
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  state TEXT NOT NULL UNIQUE,
  code_verifier TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  auth_url TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_auth_connections (
  connection_id TEXT PRIMARY KEY,
  server_id TEXT NOT NULL REFERENCES mcp_servers(server_id) ON DELETE CASCADE,
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  encrypted_access_token TEXT NOT NULL,
  encrypted_refresh_token TEXT,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_audit_events (
  audit_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  action TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL
);
"""

POSTGRES_SKILLS_REGISTRY_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS skills (
  skill_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL,
  markdown TEXT NOT NULL,
  virtual_path TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  scope TEXT NOT NULL,
  source_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
  compatibility JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (org_id, user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_skills_runtime_scope
  ON skills (org_id, user_id, enabled);

CREATE INDEX IF NOT EXISTS idx_skills_org_scope
  ON skills (org_id, scope, enabled);

CREATE TABLE IF NOT EXISTS skill_audit_events (
  audit_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  action TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL
);
"""

POSTGRES_AUDIT_HARDENING_SQL = """
-- Phase 3: tamper-evident audit log.
--
-- Each row stores an HMAC chain signature. Per-(table, org_id) chain.
-- Chain fields are nullable: rows written before this migration will
-- have NULL signature and are flagged as invalid by the verifier, which
-- is the desired behavior (customers can see exactly when chain
-- integrity began).
ALTER TABLE mcp_audit_events
  ADD COLUMN IF NOT EXISTS seq BIGINT,
  ADD COLUMN IF NOT EXISTS prev_hash BYTEA,
  ADD COLUMN IF NOT EXISTS signature BYTEA,
  ADD COLUMN IF NOT EXISTS key_version SMALLINT;

CREATE INDEX IF NOT EXISTS idx_mcp_audit_events_org_seq
  ON mcp_audit_events (org_id, seq);

ALTER TABLE skill_audit_events
  ADD COLUMN IF NOT EXISTS seq BIGINT,
  ADD COLUMN IF NOT EXISTS prev_hash BYTEA,
  ADD COLUMN IF NOT EXISTS signature BYTEA,
  ADD COLUMN IF NOT EXISTS key_version SMALLINT;

CREATE INDEX IF NOT EXISTS idx_skill_audit_events_org_seq
  ON skill_audit_events (org_id, seq);

-- Append-only role: holds INSERT/SELECT only on audit tables, no
-- UPDATE/DELETE grant. Application connections that emit audit events
-- should connect as this role so a compromised app process cannot
-- mutate history. Idempotent via DO block.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer'
  ) THEN
    CREATE ROLE audit_writer NOLOGIN;
  END IF;
END
$$;

GRANT INSERT, SELECT ON mcp_audit_events TO audit_writer;
GRANT INSERT, SELECT ON skill_audit_events TO audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON mcp_audit_events FROM audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON skill_audit_events FROM audit_writer;

-- Defense in depth: a constraint trigger that raises on any UPDATE or
-- DELETE regardless of the connecting role. Catches accidental admin
-- migrations and rules out the case where the audit_writer grant is
-- bypassed via SECURITY DEFINER functions.
CREATE OR REPLACE FUNCTION audit_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'audit log is append-only; % on % rejected',
    TG_OP, TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS mcp_audit_events_immutable ON mcp_audit_events;
CREATE TRIGGER mcp_audit_events_immutable
  BEFORE UPDATE OR DELETE ON mcp_audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();

DROP TRIGGER IF EXISTS skill_audit_events_immutable ON skill_audit_events;
CREATE TRIGGER skill_audit_events_immutable
  BEFORE UPDATE OR DELETE ON skill_audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();
"""

POSTGRES_BACKEND_MIGRATION_SQL = (
    POSTGRES_MCP_REGISTRY_MIGRATION_SQL
    + "\n"
    + POSTGRES_SKILLS_REGISTRY_MIGRATION_SQL
    + "\n"
    + POSTGRES_AUDIT_HARDENING_SQL
)
