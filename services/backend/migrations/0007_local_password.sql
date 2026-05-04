-- A4: local password authentication.
--
-- Argon2id encoded hash includes algorithm parameters; the policy table is
-- per-org so banks can require longer passwords / shorter rotation windows.
-- Reset tokens are sha256-hashed at rest so a leaked DB dump can't redeem
-- them.

CREATE TABLE IF NOT EXISTS local_credentials (
    credential_id     TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    user_id           TEXT NOT NULL REFERENCES users(user_id),
    password_hash     TEXT NOT NULL,
    password_set_at   TIMESTAMPTZ NOT NULL,
    must_rotate_at    TIMESTAMPTZ,
    last_used_at      TIMESTAMPTZ,
    previous_hashes   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    deleted_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_credentials_user
    ON local_credentials (org_id, user_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS password_policies (
    policy_id          TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL UNIQUE,
    min_length         INTEGER NOT NULL DEFAULT 12,
    require_upper      BOOLEAN NOT NULL DEFAULT TRUE,
    require_lower      BOOLEAN NOT NULL DEFAULT TRUE,
    require_digit      BOOLEAN NOT NULL DEFAULT TRUE,
    require_symbol     BOOLEAN NOT NULL DEFAULT FALSE,
    rotation_days      INTEGER,
    reuse_window       INTEGER NOT NULL DEFAULT 5,
    updated_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_id     TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    token_hash   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ,
    request_ip   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_password_reset_token_hash
    ON password_reset_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_password_reset_user_pending
    ON password_reset_tokens (user_id, expires_at) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_password_reset_expiring
    ON password_reset_tokens (expires_at) WHERE consumed_at IS NULL;
