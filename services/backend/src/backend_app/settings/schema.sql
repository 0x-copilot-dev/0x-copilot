-- Phase 12 P12-A6 — Settings module schema.
--
-- Two JSONB blob tables keyed by namespace so the wire shape can evolve
-- without a per-field migration:
--
--   tenant_settings        (NEW): one row per (tenant_id, namespace).
--                          Admin-only writes — workspace defaults that
--                          apply to every member.
--
--   user_preferences       (REUSE from migration 0018): one row per user.
--                          Namespaces live as top-level keys inside the
--                          single ``preferences`` JSONB blob. Settings
--                          deep-merges into ``notifications`` without
--                          clobbering ``home.*`` (Phase 2 +
--                          P9-A2 last_visit) or future top-level keys.
--
-- Namespaces (sub-PRD §4.4):
--   user:    "notifications"        — NotificationDefaults
--   tenant:  "notifications"        — WorkspaceNotificationDefaults
--   tenant:  "security.webhooks"    — WebhookSecurityDefaults
--
-- HMAC algo + header names ARE NOT defined here. ``security.webhooks``
-- toggles only refer to behavior; the algorithm + header constants
-- remain canonical in ``backend_app/webhooks/signer.py``.

CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    namespace           TEXT         NOT NULL CHECK (namespace IN (
                                      'notifications',
                                      'security.webhooks'
                                      )),
    settings            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by_user_id  TEXT         REFERENCES users(user_id) ON DELETE SET NULL,
    PRIMARY KEY (tenant_id, namespace)
);

CREATE INDEX IF NOT EXISTS idx_tenant_settings_namespace
    ON tenant_settings (namespace);

-- Tenant isolation via RLS (matches user_preferences / user_profiles in
-- migration 0018). Dormant until the staged stage-3 ENABLE ROW LEVEL
-- SECURITY ALTER picks up the table.
CREATE POLICY tenant_isolation ON tenant_settings
    USING (tenant_id = current_setting('app.current_org_id', true))
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_settings TO enterprise_app';
    END IF;
END
$$;
