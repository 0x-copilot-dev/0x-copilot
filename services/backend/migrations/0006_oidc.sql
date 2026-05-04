-- A3: OIDC SSO state.
--
-- Mirrors the structure of mcp_auth_sessions (state-machine row consumed
-- once) but adds OIDC-specific fields (nonce, claims_snapshot, jwks cache).
-- Refresh tokens are encrypted-at-rest via TokenVault — only ciphertext
-- here.

CREATE TABLE IF NOT EXISTS oidc_authentications (
    auth_id        TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    provider_id    TEXT NOT NULL REFERENCES auth_providers(provider_id),
    state          TEXT NOT NULL,
    nonce          TEXT NOT NULL,
    code_verifier  TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    return_to      TEXT,
    requested_at   TIMESTAMPTZ NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    ip             TEXT,
    user_agent     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oidc_auth_state
    ON oidc_authentications (state);
CREATE INDEX IF NOT EXISTS idx_oidc_auth_pending
    ON oidc_authentications (expires_at) WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS oidc_identities (
    identity_id      TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL,
    user_id          TEXT NOT NULL REFERENCES users(user_id),
    provider_id      TEXT NOT NULL REFERENCES auth_providers(provider_id),
    subject          TEXT NOT NULL,
    email_at_link    TEXT,
    linked_at        TIMESTAMPTZ NOT NULL,
    unlinked_at      TIMESTAMPTZ,
    claims_snapshot  JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- Same (provider_id, subject) re-link allowed only after unlink — partial
-- unique index excludes unlinked rows. Mirrors the soft-delete pattern in
-- A1.
CREATE UNIQUE INDEX IF NOT EXISTS idx_oidc_identity_subject
    ON oidc_identities (provider_id, subject) WHERE unlinked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oidc_identity_user
    ON oidc_identities (user_id) WHERE unlinked_at IS NULL;

CREATE TABLE IF NOT EXISTS oidc_refresh_tokens (
    token_id                 TEXT PRIMARY KEY,
    org_id                   TEXT NOT NULL,
    user_id                  TEXT NOT NULL REFERENCES users(user_id),
    provider_id              TEXT NOT NULL REFERENCES auth_providers(provider_id),
    encrypted_refresh_token  TEXT NOT NULL,
    scope                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    expires_at               TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL,
    revoked_at               TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_oidc_refresh_active
    ON oidc_refresh_tokens (org_id, user_id, provider_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oidc_refresh_expiring
    ON oidc_refresh_tokens (expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS oidc_jwks_cache (
    cache_id     TEXT PRIMARY KEY,
    provider_id  TEXT NOT NULL REFERENCES auth_providers(provider_id),
    jwks         JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oidc_jwks_provider
    ON oidc_jwks_cache (provider_id, expires_at);
