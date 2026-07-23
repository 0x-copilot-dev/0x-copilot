-- Rollback for 0046_connector_access_mode.sql.
--
-- Drops the enforcement index first (it depends on the column), then the
-- column. Safe to re-run.

DROP INDEX IF EXISTS connectors_tenant_access_mode_idx;

ALTER TABLE connectors DROP COLUMN IF EXISTS access_mode;
