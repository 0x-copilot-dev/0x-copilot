-- PR 5.1 — Login email-first IdP discovery + magic-link + workspace picker.
--
-- Adds the persistence the new entry ramp needs:
--
--   auth_provider_domains  — domain → (org_id, provider_id) join.
--                            One row per claim. Lookup keyed by the partial
--                            unique index on (domain) WHERE deleted_at IS NULL.
--                            CITEXT for case-insensitive matching.
--
--   magic_link_tokens      — one-time, 15-minute, single-use tokens. Persists
--                            sha256(plaintext); the plaintext travels in the
--                            email URL only. No row is written for emails
--                            that don't resolve to a user (anti-enumeration).
--
-- Both tables are SIDECARS on identity. login_attempts.outcome and auth_kind
-- CHECK constraints are widened to accept the new states; the existing
-- append-only audit chain tolerates additive `action` strings already.

CREATE TABLE IF NOT EXISTS auth_provider_domains (
    domain              CITEXT       NOT NULL,
    org_id              TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    provider_id         TEXT         NOT NULL REFERENCES auth_providers(provider_id) ON DELETE CASCADE,
    sso_enforced        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by_user_id  TEXT,
    deleted_at          TIMESTAMPTZ,
    PRIMARY KEY (domain, org_id, provider_id)
);
-- Single live partial index supports the discovery hot-path lookup.
CREATE INDEX IF NOT EXISTS idx_auth_provider_domains_active
    ON auth_provider_domains (domain) WHERE deleted_at IS NULL;
ALTER TABLE auth_provider_domains ENABLE ROW LEVEL SECURITY;
-- Discovery is performed by anonymous callers via the service-token path;
-- the policy below mirrors the rest of the identity surface (org bound)
-- so admin-side lookups for an unrelated org return no rows.
CREATE POLICY tenant_isolation ON auth_provider_domains
    USING (org_id = current_setting('app.current_org', true));

CREATE TABLE IF NOT EXISTS magic_link_tokens (
    token_id            TEXT         PRIMARY KEY,
    org_id              TEXT,                                            -- nullable: pre-pick path
    user_id             TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    email_lower         CITEXT       NOT NULL,
    token_hash          TEXT         NOT NULL UNIQUE,                    -- sha256(token)
    candidate_orgs      JSONB        NOT NULL DEFAULT '[]'::jsonb,
    return_to           TEXT,
    requested_ip        TEXT,
    requested_ua        TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ  NOT NULL,
    consumed_at         TIMESTAMPTZ,
    consumed_session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_user_active
    ON magic_link_tokens (user_id, created_at DESC) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_expires
    ON magic_link_tokens (expires_at) WHERE consumed_at IS NULL;
-- No RLS: the magic-link row may not have an org yet (pre-pick path), so
-- application-layer guards (token_hash UNIQUE + sha256 prefix lookup) are
-- the access boundary.

ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_outcome_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_outcome_check
    CHECK (outcome IN (
        'success','bad_password','unknown_user','locked_out','mfa_failed',
        'provider_rejected',
        'magic_link_requested','magic_link_consumed','invalid_token',
        'expired_token','consumed_token','rate_limited',
        'workspace_picker_issued','workspace_selected'
    ));

ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_auth_kind_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_auth_kind_check
    CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key','magic_link'));
