-- Rollback for 0042_provider_api_keys_default_model.sql.

ALTER TABLE provider_api_keys
    DROP COLUMN IF EXISTS default_model;
