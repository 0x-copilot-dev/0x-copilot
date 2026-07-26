-- Promote the connector slug to a stored identity on mcp_servers.
--
-- `server_id` is an installation detail whose shape depends on which surface
-- created the row: `seed:<slug>` from the catalog install, `desktop:*` from
-- the desktop OAuth coordinator. Product code that needed "which connector is
-- this" recovered the answer by parsing that id and falling back to `name` —
-- and `name` is lossy, because both mint paths write
-- `slug.replace('-', '_')`. The same connector therefore resolved to
-- `cloudflare-bindings` when seed-installed and `cloudflare_bindings` when
-- profile-installed.
--
-- Additive on purpose. No id is renumbered and no row is deleted, so every
-- existing installation keeps resolving; the application reader also falls
-- back to the historical derivation, which means correctness does not depend
-- on this backfill having run.
--
-- The backfill reproduces exactly the two historical shapes:
--   * `seed:<slug>`  -> the text after the prefix, verbatim (dashes intact);
--   * anything else  -> `name`, which is the pre-existing fallback. Dashes
--     cannot be recovered here — `name` is where they were already lost — so
--     this is a faithful backfill of the old behaviour, not a correction of
--     it. New rows state their slug and are correct from birth.

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS connector_slug text;

UPDATE mcp_servers
SET connector_slug = CASE
        WHEN server_id LIKE 'seed:%' THEN substring(server_id FROM 6)
        ELSE name
    END
WHERE connector_slug IS NULL;

-- Not NOT NULL: a row written by an older application build during a rolling
-- deploy would fail the insert. The reader tolerates NULL by design.
CREATE INDEX IF NOT EXISTS idx_mcp_servers_connector_slug
    ON mcp_servers USING btree (org_id, user_id, connector_slug);
