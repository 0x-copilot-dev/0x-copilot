-- Rollback for 0049_mcp_servers_connector_slug.sql.
--
-- Safe to run without reverting the application: `mcp_connector_slug` falls
-- back to the historical derivation whenever the slug is absent, which is the
-- same branch it takes for a row this column was never backfilled into. The
-- forward migration added no constraint and renumbered no id, so dropping the
-- column returns the table to its exact prior shape.
--
-- Dropping it does restore the lossy behaviour it was added to fix: a
-- connector installed through the desktop coordinator resolves to `name`
-- (underscored) rather than its real slug.

DROP INDEX IF EXISTS idx_mcp_servers_connector_slug;

ALTER TABLE mcp_servers
    DROP COLUMN IF EXISTS connector_slug;
