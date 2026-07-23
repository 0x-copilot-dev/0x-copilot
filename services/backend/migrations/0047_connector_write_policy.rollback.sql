-- Rollback for 0047_connector_write_policy.sql.
--
-- Drops the write-policy override column. Safe to re-run.

ALTER TABLE connectors DROP COLUMN IF EXISTS write_policy;
