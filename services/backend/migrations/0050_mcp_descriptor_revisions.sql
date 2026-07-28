-- F8: registry-owned, body-free MCP descriptor revision current views and
-- append-only invalidation feed.  Descriptor schemas, endpoint URLs, and
-- credentials never belong in either table.

CREATE TABLE IF NOT EXISTS mcp_descriptor_revisions (
    org_id text NOT NULL,
    user_id text NOT NULL,
    server_id text NOT NULL,
    profile_id text NOT NULL,
    subject_scope_hash text NOT NULL,
    revision text NOT NULL,
    config_generation bigint NOT NULL DEFAULT 0,
    auth_generation bigint NOT NULL DEFAULT 0,
    transport_generation bigint NOT NULL DEFAULT 0,
    tool_filter_generation bigint NOT NULL DEFAULT 0,
    tool_count integer NOT NULL CHECK (tool_count >= 0),
    resource_count integer NOT NULL CHECK (resource_count >= 0),
    descriptor_digest text NOT NULL,
    observed_at timestamptz NOT NULL,
    source text NOT NULL,
    PRIMARY KEY (org_id, user_id, server_id)
);

-- Generations survive an invalidation after its old complete view is removed.
-- This makes a later complete observation visibly newer without retaining a
-- stale descriptor as current state.
CREATE TABLE IF NOT EXISTS mcp_descriptor_revision_generations (
    org_id text NOT NULL,
    user_id text NOT NULL,
    server_id text NOT NULL,
    config_generation bigint NOT NULL DEFAULT 0,
    auth_generation bigint NOT NULL DEFAULT 0,
    transport_generation bigint NOT NULL DEFAULT 0,
    tool_filter_generation bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, user_id, server_id)
);

CREATE TABLE IF NOT EXISTS mcp_descriptor_revision_notices (
    sequence_no bigserial PRIMARY KEY,
    notice_id text NOT NULL UNIQUE,
    cursor text NOT NULL UNIQUE,
    org_id text NOT NULL,
    user_id text NOT NULL,
    server_id text NOT NULL,
    profile_id text NOT NULL,
    subject_scope_hash text NOT NULL,
    old_revision text,
    new_revision text,
    reason text NOT NULL,
    occurred_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_descriptor_revision_notices_feed
    ON mcp_descriptor_revision_notices (org_id, user_id, sequence_no);

-- Stable retry key; the copied fields mean an idempotent publish can return
-- the original view after later revisions have superseded current state.
CREATE TABLE IF NOT EXISTS mcp_descriptor_revision_idempotency (
    org_id text NOT NULL,
    user_id text NOT NULL,
    server_id text NOT NULL,
    idempotency_key text NOT NULL,
    profile_id text NOT NULL,
    subject_scope_hash text NOT NULL,
    revision text NOT NULL,
    config_generation bigint NOT NULL,
    auth_generation bigint NOT NULL,
    transport_generation bigint NOT NULL,
    tool_filter_generation bigint NOT NULL,
    tool_count integer NOT NULL,
    resource_count integer NOT NULL,
    descriptor_digest text NOT NULL,
    observed_at timestamptz NOT NULL,
    source text NOT NULL,
    PRIMARY KEY (org_id, user_id, server_id, idempotency_key)
);

-- Match the backend's tenant-isolation convention.  API transactions stamp
-- app.current_org_id; the service role is allowed only for migration/admin
-- operations through the existing deployment role policy.
ALTER TABLE mcp_descriptor_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_descriptor_revision_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_descriptor_revision_notices ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_descriptor_revision_idempotency ENABLE ROW LEVEL SECURITY;

CREATE POLICY mcp_descriptor_revisions_tenant_isolation ON mcp_descriptor_revisions
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY mcp_descriptor_revision_generations_tenant_isolation ON mcp_descriptor_revision_generations
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY mcp_descriptor_revision_notices_tenant_isolation ON mcp_descriptor_revision_notices
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
CREATE POLICY mcp_descriptor_revision_idempotency_tenant_isolation ON mcp_descriptor_revision_idempotency
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
