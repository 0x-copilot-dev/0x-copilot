-- PR 4.4.6 — connector card description.
--
-- One additional nullable column on ``mcp_servers``. The catalog endpoint
-- ships ``description`` (a one-liner like "Workspace pages and databases")
-- and the install path copies it onto the row so the Settings → Connectors
-- card and the Connected tab can render it without re-fetching the catalog.
--
-- Stored as TEXT NOT NULL DEFAULT '' so the row mapper never has to
-- sentinel-handle nulls; legacy (custom URL) rows arrive with the empty
-- string, which the FE renders as the existing status hint fallback.

BEGIN;

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';

COMMIT;
