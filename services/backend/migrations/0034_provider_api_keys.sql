-- Phase 2 BYOK — per-user provider API keys, encrypted at rest.
--
-- One row per (org, user, provider). ``encrypted_key`` is a TokenVault
-- envelope (Fernet in dev, KMS in production) — plaintext NEVER lands
-- in this table. ``key_hint`` carries only the last 4 characters for
-- the Settings UI listing ("…1234"); the full key is decrypted solely
-- on the service-token-only internal lane
-- (``GET /internal/v1/policies/runtime``) consumed by ai-backend at
-- run start.
--
-- Providers are the closed set the runtime supports today; adding a
-- provider is a migration (CHECK constraint) + enum change in
-- ``backend_app/provider_keys/store.py`` — deliberate friction so the
-- runtime and storage never drift.

CREATE TABLE IF NOT EXISTS provider_api_keys (
    org_id        TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    provider      TEXT         NOT NULL CHECK (provider IN ('openai', 'anthropic', 'google')),
    encrypted_key TEXT         NOT NULL,
    key_hint      TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, user_id, provider)
);

-- Settings UI hot path: list every stored key for one user.
CREATE INDEX IF NOT EXISTS provider_api_keys_user_idx
    ON provider_api_keys (org_id, user_id);

-- Tenant isolation via RLS — matches the policy shape on every
-- product table (see 0032_todos).
ALTER TABLE provider_api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY provider_api_keys_tenant_isolation ON provider_api_keys
    USING (
        org_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON provider_api_keys TO enterprise_app';
    END IF;
END
$$;
