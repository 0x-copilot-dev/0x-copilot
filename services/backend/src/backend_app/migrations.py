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
