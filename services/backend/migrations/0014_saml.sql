-- A5: SAML 2.0 SSO state.
--
-- Mirrors the structure of oidc_authentications + oidc_identities (PR 0006):
-- a single-use authn-request row consumed when the IdP POSTs its assertion
-- to our ACS endpoint, plus a long-lived (provider_id, name_id) → user_id
-- linking row.
--
-- ``assertion_id`` is the replay guard — every accepted assertion writes
-- one row, the UNIQUE index slams the door on a second POST of the same
-- XML. We persist the row even on validation failure (for audit) but
-- ``status='rejected'`` keeps it out of the active set.
--
-- Encrypted-assertion support: the ``sp_signing_key_ref`` /
-- ``sp_decryption_key_ref`` keys live on ``auth_providers.config`` JSONB
-- (no schema change here) so a follow-up that wires assertion encryption
-- doesn't have to migrate again.
--
-- RLS: skipped here, matching 0011_mfa / 0012_account_lockouts. A future
-- post-RLS sweep enables tenant_isolation policies on this table family.

CREATE TABLE IF NOT EXISTS saml_authentications (
    auth_id        TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    provider_id    TEXT NOT NULL REFERENCES auth_providers(provider_id),
    request_id     TEXT,                              -- SP-initiated only
    assertion_id   TEXT NOT NULL,                     -- replay guard
    relay_state    TEXT,
    status         TEXT NOT NULL CHECK (status IN ('pending','consumed','rejected')),
    requested_at   TIMESTAMPTZ NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    ip             TEXT,
    user_agent     TEXT
);
-- Replay defense: any second POST of the same assertion is refused at
-- the DB, regardless of which row claimed it first.
CREATE UNIQUE INDEX IF NOT EXISTS idx_saml_assertion_replay
    ON saml_authentications (assertion_id);
-- SP-initiated lookup: we get the IdP's response, fetch our pending row
-- by request_id to validate InResponseTo.
CREATE INDEX IF NOT EXISTS idx_saml_request
    ON saml_authentications (request_id) WHERE request_id IS NOT NULL;
-- Pending-row sweeper would target this index.
CREATE INDEX IF NOT EXISTS idx_saml_pending
    ON saml_authentications (expires_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS saml_identities (
    identity_id      TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL,
    user_id          TEXT NOT NULL REFERENCES users(user_id),
    provider_id      TEXT NOT NULL REFERENCES auth_providers(provider_id),
    name_id          TEXT NOT NULL,
    name_id_format   TEXT NOT NULL,
    linked_at        TIMESTAMPTZ NOT NULL,
    unlinked_at      TIMESTAMPTZ,
    attributes_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- Same (provider_id, name_id) re-link allowed only after unlink — partial
-- unique index excludes unlinked rows. Mirrors oidc_identities' soft-delete
-- pattern.
CREATE UNIQUE INDEX IF NOT EXISTS idx_saml_identity_nameid
    ON saml_identities (provider_id, name_id) WHERE unlinked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_saml_identity_user
    ON saml_identities (user_id) WHERE unlinked_at IS NULL;
