-- Rollback for 0051_mcp_servers_headers_stdio.sql.
--
-- Restoring `url NOT NULL` is only safe once no stdio server exists, since
-- those rows carry a NULL url by design. Delete them first — they are
-- unusable without the code this migration accompanies, so dropping them
-- loses no working configuration.

DELETE FROM mcp_servers
WHERE url IS NULL;

ALTER TABLE mcp_servers
    ALTER COLUMN url SET NOT NULL;

ALTER TABLE mcp_servers
    DROP COLUMN IF EXISTS stdio;

ALTER TABLE mcp_servers
    DROP COLUMN IF EXISTS headers;
