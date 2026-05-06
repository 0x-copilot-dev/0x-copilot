DROP INDEX IF EXISTS idx_api_keys_workspace;
ALTER TABLE api_keys DROP COLUMN IF EXISTS kind;
