-- Rollback PR 7.2 per-connector token attribution.
DROP POLICY IF EXISTS tenant_isolation ON runtime_usage_daily_connector;
ALTER TABLE IF EXISTS runtime_usage_daily_connector DISABLE ROW LEVEL SECURITY;
DROP TABLE IF EXISTS runtime_usage_daily_connector;
DROP INDEX IF EXISTS idx_runtime_model_call_usage_org_connector_created;
ALTER TABLE runtime_model_call_usage DROP COLUMN IF EXISTS connector_slug;
