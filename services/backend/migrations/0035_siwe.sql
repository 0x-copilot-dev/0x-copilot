-- Sign-In-With-Ethereum (EIP-4361).
--
-- Two sidecar tables on identity:
--
--   siwe_nonces        — single-use sign-in nonces (mirrors the
--                        oidc_authentications consume-once state machine).
--                        Bound to the requesting address + chain so a nonce
--                        minted for one wallet cannot authenticate another.
--                        No org: rows exist pre-identity (same reasoning as
--                        magic_link_tokens' org-less path), so no RLS —
--                        application-layer guards (UNIQUE nonce + atomic
--                        consume) are the isolation story.
--
--   wallet_identities  — wallet address → local user link (the SIWE
--                        analogue of oidc_identities). Address is stored
--                        lowercase; CITEXT + UNIQUE makes the column
--                        case-insensitive at the DB level. One row per
--                        address across the whole deployment.
--
-- login_attempts.auth_kind and organization_members.source CHECK
-- constraints are widened for the new 'siwe' value (same drop+recreate
-- pattern as 0019/0020 — the only way to widen a CHECK in Postgres).

CREATE TABLE IF NOT EXISTS siwe_nonces (
    nonce_id     TEXT         PRIMARY KEY,
    nonce        TEXT         NOT NULL,
    address      TEXT         NOT NULL,
    chain_id     BIGINT       NOT NULL,
    issued_at    TIMESTAMPTZ  NOT NULL,
    expires_at   TIMESTAMPTZ  NOT NULL,
    consumed_at  TIMESTAMPTZ,
    ip           TEXT,
    user_agent   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_siwe_nonces_nonce
    ON siwe_nonces (nonce);
-- Sweeper scan for expired unconsumed nonces.
CREATE INDEX IF NOT EXISTS idx_siwe_nonces_pending
    ON siwe_nonces (expires_at) WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS wallet_identities (
    wallet_id   TEXT         PRIMARY KEY,
    address     CITEXT       NOT NULL,
    org_id      TEXT         NOT NULL REFERENCES organizations(org_id),
    user_id     TEXT         NOT NULL REFERENCES users(user_id),
    chain_id    BIGINT       NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_identities_address
    ON wallet_identities (address);
CREATE INDEX IF NOT EXISTS idx_wallet_identities_user
    ON wallet_identities (user_id);

ALTER TABLE wallet_identities ENABLE ROW LEVEL SECURITY;
-- The verify hot path reads by address via the service-token connection
-- (table owner, pre-identity — same situation as oidc_identities lookups);
-- the policy scopes admin-side org queries, mirroring 0020's
-- auth_provider_domains.
CREATE POLICY tenant_isolation ON wallet_identities
    USING (org_id = current_setting('app.current_org', true));

-- Widen login_attempts.auth_kind for 'siwe'.
ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_auth_kind_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_auth_kind_check
    CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key','magic_link','siwe'));

-- Widen organization_members.source for 'siwe'.
ALTER TABLE organization_members DROP CONSTRAINT IF EXISTS organization_members_source_check;
ALTER TABLE organization_members ADD CONSTRAINT organization_members_source_check
    CHECK (source IN ('local','oidc','saml','scim','bootstrap','invite','siwe'));
