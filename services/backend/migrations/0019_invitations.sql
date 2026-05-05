-- PR 4.2 — invitations table + INVITE member source.
--
-- Mirrors the SCIM-token mint pattern (0015_scim.sql:34-49):
--   token_hash  = sha256(plaintext); plaintext returned exactly once.
--   token_prefix = visible 8-char head so admins can identify a row in the
--                   pending-list UI without re-revealing the secret.
--
-- One row per invitation. Accept and revoke are soft (timestamps), not row
-- deletions. The partial unique-active index on (org_id, lower(email)) keeps
-- "at most one outstanding invite per email per org" without preventing
-- re-issue after revoke / acceptance.
--
-- The accept endpoint is unauthenticated; it dispatches by token_hash, so
-- the unique index on token_hash is the lookup key.

CREATE TABLE IF NOT EXISTS invitations (
    invite_id            TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL REFERENCES organizations(org_id),
    email                CITEXT NOT NULL,
    role_id              TEXT NOT NULL REFERENCES roles(role_id),
    token_hash           TEXT NOT NULL,
    token_prefix         TEXT NOT NULL,
    created_by_user_id   TEXT NOT NULL REFERENCES users(user_id),
    created_at           TIMESTAMPTZ NOT NULL,
    expires_at           TIMESTAMPTZ NOT NULL,
    accepted_at          TIMESTAMPTZ,
    accepted_user_id     TEXT REFERENCES users(user_id),
    revoked_at           TIMESTAMPTZ,
    revoked_by_user_id   TEXT REFERENCES users(user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_token_hash
    ON invitations (token_hash);
CREATE INDEX IF NOT EXISTS idx_invitations_org_pending
    ON invitations (org_id, expires_at DESC)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_org_email_active
    ON invitations (org_id, lower(email))
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- Add 'invite' to the organization_members.source CHECK constraint.
-- Drop + recreate is the only way to widen a CHECK constraint in Postgres.
ALTER TABLE organization_members DROP CONSTRAINT IF EXISTS organization_members_source_check;
ALTER TABLE organization_members ADD CONSTRAINT organization_members_source_check
    CHECK (source IN ('local','oidc','saml','scim','bootstrap','invite'));
