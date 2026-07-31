-- Configured request headers and local (stdio) MCP servers.
--
-- Two capabilities the registry could describe but not carry:
--
--   headers  A remote server authenticated by a static credential — a GitHub
--            PAT, a vendor API key — had nowhere to put it. `auth_mode` has
--            listed `api_key` since the baseline, but no column ever held the
--            key, so the enum value was decorative and OAuth was the only
--            authentication that worked. Stored as a JSON array of
--            `McpConfiguredValue`, whose secret member is ciphertext from
--            `TokenVault`; literals (`X-Api-Version: 2`) stay plaintext on
--            purpose so the config editor can round-trip them.
--
--   stdio    `McpTransport.STDIO` has likewise existed since the baseline with
--            no implementation behind it — the only transport is HTTP. A local
--            server is a subprocess, so it needs a command/args/env/cwd, none
--            of which the row could express.
--
-- `url` becomes nullable because a stdio server genuinely has no endpoint. The
-- application enforces the pairing (`McpServerRecord._transport_matches_address`):
-- exactly one of `url` or `stdio` addresses a server. A CHECK constraint would
-- state that here too, but it would fail closed against rows written by an
-- older application build during a rolling deploy, so the invariant lives in
-- the one place that can also explain itself to the user.
--
-- Additive. Existing rows get `headers = []` and `stdio = NULL`, which is
-- exactly what they mean today, so no backfill is needed and no reader
-- changes behaviour until a user configures one of these.

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS headers jsonb DEFAULT '[]'::jsonb NOT NULL;

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS stdio jsonb;

ALTER TABLE mcp_servers
    ALTER COLUMN url DROP NOT NULL;
