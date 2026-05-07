-- PR 3.4.1 — connector popover brand fidelity.
--
-- Five additive columns on ``mcp_servers`` so the per-chat connector
-- popover can render real brand favicons, scope subtitles, and resume
-- a paused connector with a non-empty default-scopes payload.
--
-- - ``logo_url``       : optional CDN-hosted SVG; frontend ``<img>`` falls
--                        through to the design-system letter glyph on 404.
-- - ``brand_color``    : optional hex / oklch chip background.
-- - ``scopes_summary`` : optional one-line natural-language summary of
--                        what the connector is allowed to do.
-- - ``default_scopes`` : JSONB array of scope ids; PR 1.2's PATCH endpoint
--                        round-trips this as the resume target so a
--                        Resume-from-Paused row no longer flips the
--                        connector on with ``[]`` (the PR 3.4 default).
-- - ``admin_managed``  : true when only workspace admins may toggle the
--                        ``enabled`` bit on the row (consumed by the
--                        popover to disable the Enable button for
--                        non-admins).
--
-- Backfill writes catalog values into rows seeded via
-- ``seed:<slug>`` server_ids. The UPDATE only touches rows whose new
-- columns are still null / empty so subsequent re-runs are idempotent
-- and admin overrides survive.

BEGIN;

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS logo_url       TEXT,
    ADD COLUMN IF NOT EXISTS brand_color    TEXT,
    ADD COLUMN IF NOT EXISTS scopes_summary TEXT,
    ADD COLUMN IF NOT EXISTS default_scopes JSONB   NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS admin_managed  BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill the seeded catalog rows. ``server_id = 'seed:<slug>'`` is
-- stable and unique per (org_id, user_id) pair (server_id is the table's
-- primary key). Idempotent: re-running this only writes when columns are
-- still at their defaults so admin overrides persist across re-runs.
UPDATE mcp_servers AS m SET
    logo_url       = COALESCE(m.logo_url,       c.logo_url),
    brand_color    = COALESCE(m.brand_color,    c.brand_color),
    scopes_summary = COALESCE(m.scopes_summary, c.scopes_summary),
    default_scopes = CASE
                         WHEN m.default_scopes = '[]'::jsonb THEN c.default_scopes
                         ELSE m.default_scopes
                     END
FROM (VALUES
    ('seed:asana',                    'https://cdn.atlas.local/brand/asana.svg',      '#F06A6A', 'Read tasks, comment, no delete',         '["read","comment"]'::jsonb),
    ('seed:atlassian',                'https://cdn.atlas.local/brand/atlassian.svg',  '#2684FF', 'Read issues and Confluence pages',       '["read"]'::jsonb),
    ('seed:cloudflare-bindings',      'https://cdn.atlas.local/brand/cloudflare.svg', '#F38020', 'Read Workers bindings',                  '["read"]'::jsonb),
    ('seed:cloudflare-observability', 'https://cdn.atlas.local/brand/cloudflare.svg', '#F38020', 'Read logs, traces, and metrics',         '["read"]'::jsonb),
    ('seed:github',                   'https://cdn.atlas.local/brand/github.svg',     '#0D1117', 'Read repos, no write',                   '["read"]'::jsonb),
    ('seed:intercom',                 'https://cdn.atlas.local/brand/intercom.svg',   '#1F8DED', 'Read conversations and contacts',        '["read"]'::jsonb),
    ('seed:linear',                   'https://cdn.atlas.local/brand/linear.svg',     '#5E6AD2', 'Read issues, projects, cycles',          '["read"]'::jsonb),
    ('seed:notion',                   'https://cdn.atlas.local/brand/notion.svg',     '#000000', 'Read all pages, write to /Drafts',       '["read","write_drafts"]'::jsonb),
    ('seed:paypal',                   'https://cdn.atlas.local/brand/paypal.svg',     '#003087', 'Read payments and invoices',             '["read"]'::jsonb),
    ('seed:plaid',                    'https://cdn.atlas.local/brand/plaid.svg',      '#111111', 'Read accounts and transactions',         '["read"]'::jsonb),
    ('seed:sentry',                   'https://cdn.atlas.local/brand/sentry.svg',     '#362D59', 'Read issues and stack traces',           '["read"]'::jsonb),
    ('seed:square',                   'https://cdn.atlas.local/brand/square.svg',     '#000000', 'Read payments, orders, inventory',       '["read"]'::jsonb),
    ('seed:zapier',                   'https://cdn.atlas.local/brand/zapier.svg',     '#FF4A00', 'Run cross-app automations',              '["read","trigger"]'::jsonb)
) AS c (server_id, logo_url, brand_color, scopes_summary, default_scopes)
WHERE m.server_id = c.server_id;

COMMIT;
