-- A2: server-issued sessions table.
--
-- Bound to the bearer token via:
--   * session_id  → the `sid` claim in the token payload
--   * token_hash  → sha256(token signature) so a leaked DB dump is unusable
--
-- Plaintext bearer is NEVER stored. Revocation is a row update; the next
-- non-cached request returns 401.

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL REFERENCES users(user_id),
    token_hash          TEXT NOT NULL,
    roles               JSONB NOT NULL DEFAULT '[]'::jsonb,
    permission_scopes   JSONB NOT NULL DEFAULT '[]'::jsonb,
    connector_scopes    JSONB NOT NULL DEFAULT '{}'::jsonb,
    auth_provider_id    TEXT,
    mfa_satisfied_at    TIMESTAMPTZ,
    client_ip           TEXT,
    user_agent          TEXT,
    device_label        TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revocation_reason   TEXT
);

-- Active token lookup. Partial index so a freshly minted token never collides
-- with a previously revoked one that happens to land on the same hash
-- (cryptographically improbable but the schema should not depend on it).
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token_active
    ON sessions (token_hash) WHERE revoked_at IS NULL;

-- "List my sessions" + revocation-cascade scans.
CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON sessions (org_id, user_id, revoked_at, expires_at);

-- Sweeper scan — find expired non-revoked sessions in O(log N).
CREATE INDEX IF NOT EXISTS idx_sessions_expiring
    ON sessions (expires_at) WHERE revoked_at IS NULL;
