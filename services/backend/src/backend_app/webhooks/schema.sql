-- Phase 11 — Connectors destination webhook manager schema. Single source
-- of truth; the migration file at
-- ``services/backend/migrations/<NNNN>_connector_webhooks.sql`` is a verbatim
-- copy so the migration runner picks it up at boot.
--
-- Cross references:
--   * connectors-prd.md §5.2 (webhooks schema)
--   * connectors-prd.md §9 (HMAC security UX — Routines §9.7 Q6 lands here)
--   * routines schema.sql (Phase 5 — `routine_audit_events` chain stays
--     authoritative for routine state changes; webhook audit rows live
--     in `webhook_audit_events` below to keep the routines table's
--     action taxonomy free of webhook noise)
--
-- Tenant isolation is via RLS, identical to every other product table.

CREATE TABLE IF NOT EXISTS webhooks (
    id                      TEXT          PRIMARY KEY,                       -- trig_<ulid>
    tenant_id               TEXT          NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    owner_user_id           TEXT          NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    url                     TEXT          NOT NULL CHECK (url LIKE 'https://%'),
    secret_strategy         TEXT          NOT NULL CHECK (
        secret_strategy IN ('rotating','static')
    ),
    hmac_algo               TEXT          NOT NULL DEFAULT 'hmac-sha256' CHECK (
        hmac_algo IN ('hmac-sha256')
    ),
    ip_allowlist            TEXT[]        NOT NULL DEFAULT '{}',                -- CIDR strings; empty = no restriction
    status                  TEXT          NOT NULL DEFAULT 'active' CHECK (
        status IN ('active','paused')
    ),
    last_fire_at            TIMESTAMPTZ,
    last_status_code        INTEGER,
    routine_id              TEXT,                                                -- nullable; FK soft via service layer
    -- Opaque token-vault pointer. Plaintext is never persisted; on rotation the
    -- current secret moves into ``previous_*`` for the 14-day grace per
    -- connectors-prd §9.2.
    vault_ref               TEXT          NOT NULL,
    previous_vault_ref      TEXT,
    previous_expires_at     TIMESTAMPTZ,
    rotates_at              TIMESTAMPTZ,                                          -- when the worker should rotate (NULL = never; static rows)
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    deleted_at              TIMESTAMPTZ
);

-- Rotation worker hot path: claim rows whose rotation is due. Partial index
-- so the rotation loop never scans paused / deleted rows.
CREATE INDEX IF NOT EXISTS webhooks_rotation_due_idx
    ON webhooks (rotates_at)
    WHERE secret_strategy = 'rotating'
      AND status = 'active'
      AND deleted_at IS NULL;

-- Per-tenant list path (status + creation order).
CREATE INDEX IF NOT EXISTS webhooks_tenant_status_idx
    ON webhooks (tenant_id, status, created_at DESC)
    WHERE deleted_at IS NULL;

-- Routine reverse-lookup for the routine detail page + cascade-on-delete.
CREATE INDEX IF NOT EXISTS webhooks_routine_idx
    ON webhooks (tenant_id, routine_id)
    WHERE deleted_at IS NULL AND routine_id IS NOT NULL;

ALTER TABLE webhooks ENABLE ROW LEVEL SECURITY;

CREATE POLICY webhooks_tenant_isolation ON webhooks
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Webhook audit events — append-only; create/rotate/delete/test-fire all
-- funnel through this table. Schema mirrors routine_audit_events so the
-- audit-chain signer reuses the same column layout.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS webhook_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Dotted action taxonomy per connectors-prd §6.2:
    --   webhook.created / webhook.updated / webhook.rotated
    --   webhook.deleted / webhook.test_fired / webhook.rotation_due
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'webhook',
    target_id           TEXT         NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    correlation_id      TEXT,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- audit-chain integration; same shape as routine_audit_events.
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS webhook_audit_tenant_idx
    ON webhook_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS webhook_audit_target_idx
    ON webhook_audit_events (tenant_id, target_id, ts);

ALTER TABLE webhook_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY webhook_audit_tenant_isolation ON webhook_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON webhooks TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON webhook_audit_events TO enterprise_app';
    END IF;
END
$$;
