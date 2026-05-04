-- C3 atomicity fix: required by ON CONFLICT (server_id) target on
-- mcp_auth_connections. The prior DELETE-then-INSERT in PostgresMcpStore
-- left a window with no token after a kill between the two statements.
-- The new put_token uses a single INSERT ... ON CONFLICT DO UPDATE keyed
-- by server_id and guarded by a cross-tenant WHERE clause; that requires
-- a unique constraint on the conflict target.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_auth_connections_server
    ON mcp_auth_connections (server_id);
