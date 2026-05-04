ALTER TABLE runtime_model_call_usage
    DROP COLUMN IF EXISTS pricing_version,
    DROP COLUMN IF EXISTS pricing_id,
    DROP COLUMN IF EXISTS cost_micro_usd;

ALTER TABLE runtime_run_usage
    DROP COLUMN IF EXISTS pricing_version,
    DROP COLUMN IF EXISTS pricing_id,
    DROP COLUMN IF EXISTS cost_micro_usd;

DROP INDEX IF EXISTS idx_model_pricing_active;
DROP INDEX IF EXISTS idx_model_pricing_lookup;
DROP TABLE IF EXISTS model_pricing;
