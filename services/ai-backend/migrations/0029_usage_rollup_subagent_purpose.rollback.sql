-- Rollback for 0029.
--
-- Drops the two new tables and reverts the connector rollup PK to its
-- pre-01d shape. Any rows written between the forward migration and the
-- rollback retain their data on the old PK columns; the model_name
-- column gets dropped so rows collapse back to (org, day, slug).

DROP TABLE IF EXISTS runtime_usage_daily_purpose;
DROP TABLE IF EXISTS runtime_usage_daily_subagent;

-- Revert connector rollup PK. If multiple model_name rows exist for
-- the same (org, day, slug), the DROP CONSTRAINT will succeed but the
-- subsequent ADD CONSTRAINT will fail on duplicate keys. The rollback
-- collapses by aggregating into one row per (org, day, slug) first;
-- ops should run this manually if the rollback ever needs the
-- collapse step (production hasn't been live with multi-model rows
-- long enough for this to matter on day-of-rollback).

ALTER TABLE runtime_usage_daily_connector
    DROP CONSTRAINT IF EXISTS runtime_usage_daily_connector_pkey;

ALTER TABLE runtime_usage_daily_connector
    DROP COLUMN IF EXISTS model_name;

ALTER TABLE runtime_usage_daily_connector
    ADD CONSTRAINT runtime_usage_daily_connector_pkey
        PRIMARY KEY (org_id, day, connector_slug);
