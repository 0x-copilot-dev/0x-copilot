BEGIN;

ALTER TABLE mcp_servers
    DROP COLUMN IF EXISTS admin_managed,
    DROP COLUMN IF EXISTS default_scopes,
    DROP COLUMN IF EXISTS scopes_summary,
    DROP COLUMN IF EXISTS brand_color,
    DROP COLUMN IF EXISTS logo_url;

COMMIT;
