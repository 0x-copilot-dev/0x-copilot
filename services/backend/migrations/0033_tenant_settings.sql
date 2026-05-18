-- Phase 12 P12-A6 — Settings module: tenant_settings JSONB blob table.
--
-- One row per (tenant_id, namespace) so the wire shape can evolve
-- without a per-field migration. Workspace defaults live here:
--
--   namespace = 'notifications'        -> WorkspaceNotificationDefaults
--   namespace = 'security.webhooks'    -> WebhookSecurityDefaults
--
-- User-scoped settings ("notifications" for a single user, plus the
-- existing "home.*" preferences from migration 0018 + P9-A2) live in
-- ``user_preferences`` and are deep-merged at the service layer so a
-- PATCH against one namespace never clobbers another.
--
-- HMAC algorithm + header names ARE NOT defined here. The
-- ``security.webhooks`` blob carries behavioural toggles only — the
-- canonical algorithm + header constants stay in
-- ``backend_app/webhooks/signer.py``.

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

-- Tenant isolation via RLS, mirrors user_profiles + user_preferences in
-- migration 0018. Dormant until the stage-3 ENABLE RLS ALTER picks up
-- this table.
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
