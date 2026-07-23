-- Rollback 0003_usage_call_attribution: drop the covering index and both
-- per-call attribution columns. Reverses the additive ALTER exactly.

DROP INDEX IF EXISTS idx_runtime_model_call_usage_org_user_created;
ALTER TABLE runtime_model_call_usage DROP COLUMN IF EXISTS surface_id;
ALTER TABLE runtime_model_call_usage DROP COLUMN IF EXISTS user_id;
