DROP INDEX IF EXISTS idx_mcp_auth_connections_kms_key_id;

ALTER TABLE mcp_auth_connections
    DROP COLUMN IF EXISTS kms_key_id;
