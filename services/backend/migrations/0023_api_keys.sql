-- PR B3: personal API keys (atlas_pk_… bearer for CI / scripts).
--
-- The plaintext secret is shown ONCE on creation; the server stores
-- only the hash. Bearer auth path is `atlas_pk_<prefix>_<secret>`:
--   1. Split on `_`. Lookup row by `key_prefix`.
--   2. Constant-time compare HMAC(secret, server_pepper) to
--      stored_secret_hash.
--   3. If match: stamp last_used_at, mint identity from {org_id,
--      user_id} and proceed under that identity.
--
-- A key inherits its owner's roles + permission_scopes; per-key scope
-- restrictions can narrow but never widen (`scopes` ⊆ user's scopes).
-- Rotation is "create new + delete old" with the new row's
-- `rotated_from_id` pointing at the old row's id for forensic
-- continuity.

CREATE TABLE IF NOT EXISTS api_keys (
    id                  TEXT         PRIMARY KEY,
    org_id              TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    user_id             TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    label               TEXT         NOT NULL,
    key_prefix          TEXT         NOT NULL UNIQUE,
    secret_hash         TEXT         NOT NULL,
    scopes              JSONB        NOT NULL DEFAULT '[]'::jsonb,
    last_used_at        TIMESTAMPTZ,
    last_used_ip        TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    rotated_from_id     TEXT,
    revoked_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user
    ON api_keys (user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_org
    ON api_keys (org_id) WHERE revoked_at IS NULL;

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON api_keys
    USING (org_id = current_setting('app.current_org', true));
