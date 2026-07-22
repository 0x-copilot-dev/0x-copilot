-- PRD-I PR-I.3 — connectors destination durable read model.
--
-- Brings the connectors tables (previously module-local
-- ``backend_app/connectors/schema.sql`` only — never in the migration
-- chain) into the versioned chain so the durable
-- ``PostgresConnectorsStore`` has real DDL behind it. Content matches
-- the module schema byte-for-semantics: denormalized read model over
-- ``mcp_servers`` + ``token_vault`` metadata (connectors-prd §3.2),
-- kept in sync by the write-through helper
-- (``upsert_from_mcp_registration``).
--
-- Authorization is service-layer (tenant member reads, owner-or-admin
-- writes, 404-not-403) plus RLS for tenant isolation.

CREATE TABLE IF NOT EXISTS connectors (
    id              TEXT         PRIMARY KEY,
    tenant_id       TEXT         NOT NULL,
    slug            TEXT         NOT NULL,
    display_name    TEXT         NOT NULL,
    description     TEXT         NOT NULL DEFAULT '',
    -- Connector status taxonomy: see connectors-prd §1.6.
    --   connected | disconnected | error | expired
    status          TEXT         NOT NULL DEFAULT 'connected' CHECK (
        status IN ('connected', 'disconnected', 'error', 'expired')
    ),
    status_reason   TEXT,
    owner_user_id   TEXT         NOT NULL,
    -- JSONB array of ConnectorScopeEntry. Provider-specific scope
    -- strings; description sourced from the catalog at backend bootstrap.
    scopes          JSONB        NOT NULL DEFAULT '[]'::jsonb,
    last_sync_at    TIMESTAMPTZ,
    last_error_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Opaque pointer into `token_vault`. The vault row is the source of
    -- truth for tokens; this column lets the read endpoint render a
    -- "needs reconnect" badge without joining.
    vault_ref       TEXT         NOT NULL
);

-- Hot list endpoint: per-tenant by status + slug for the
-- Connected/Available/Custom tabs.
CREATE INDEX IF NOT EXISTS connectors_tenant_status_slug_idx
    ON connectors (tenant_id, status, slug);

-- "What does USER X own?" — owner-scoped writes path.
CREATE INDEX IF NOT EXISTS connectors_tenant_owner_idx
    ON connectors (tenant_id, owner_user_id);

-- Available-catalog lookup (per slug, across tenants — admin marketplace
-- queries; the dest endpoint scopes by tenant_id additionally).
CREATE INDEX IF NOT EXISTS connectors_slug_idx
    ON connectors (slug);

ALTER TABLE connectors ENABLE ROW LEVEL SECURITY;

CREATE POLICY connectors_tenant_isolation ON connectors
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- Audit events — append-only; mutating writes (status / scope / token
-- refresh / disconnect) emit one row each per connectors-prd §6.2.
-- seq / prev_hash / signature / key_version are the tamper-evident
-- audit-chain columns (packages/audit-chain), same shape as
-- inbox_audit_events / project_audit_events.

CREATE TABLE IF NOT EXISTS connector_audit_events (
    audit_id        TEXT         PRIMARY KEY,
    tenant_id       TEXT         NOT NULL,
    actor_user_id   TEXT         NOT NULL,
    -- Dotted action taxonomy per connectors-prd §6.2:
    --   connector.connected / connector.disconnected / connector.expired
    --   connector.scope_added / connector.scope_removed
    --   connector.error / connector.token_refreshed
    action          TEXT         NOT NULL,
    target_kind     TEXT         NOT NULL DEFAULT 'connector',
    target_id       TEXT         NOT NULL,
    before_state    JSONB,
    after_state     JSONB,
    correlation_id  TEXT,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- audit-chain integration; same shape as inbox_audit_events.
    seq             BIGINT,
    prev_hash       BYTEA,
    signature       BYTEA,
    key_version     INTEGER
);

CREATE INDEX IF NOT EXISTS connector_audit_tenant_idx
    ON connector_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS connector_audit_target_idx
    ON connector_audit_events (tenant_id, target_id, ts);

ALTER TABLE connector_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY connector_audit_tenant_isolation ON connector_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON connectors TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON connector_audit_events TO enterprise_app';
    END IF;
END
$$;
