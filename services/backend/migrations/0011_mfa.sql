-- A6: MFA — TOTP (RFC 6238 / NIST AAL2) + WebAuthn (FIDO2 / NIST AAL3) +
-- recovery codes + per-org enforcement.
--
-- Five tables:
--   mfa_factors          — generic per-user factor enrollment (kind says
--                          which detail table to JOIN).
--   totp_secrets         — encrypted seed + last_step replay guard for
--                          TOTP factors. ``encrypted_secret`` rides the
--                          existing TokenVault adapter (KMS-backed in C6).
--   webauthn_credentials — COSE public key + sign_count for FIDO2.
--   mfa_challenges       — single-use nonce binding a verify request to
--                          a specific user/factor; ``consumed_at`` flips
--                          via UPDATE...RETURNING so two workers can't
--                          satisfy the same challenge twice.
--   mfa_recovery_codes   — one-shot fallback when a user loses their
--                          factor; only sha256 hashes stored.
--
-- ``sessions.mfa_satisfied_at`` already exists (added in 0005); this PR
-- populates it. ``identity_policies.mfa_required`` stays in a follow-up
-- migration if the SAML/SCIM agent doesn't add it first — for now the
-- per-org gate lives in the org's policy lookup.

CREATE TABLE IF NOT EXISTS mfa_factors (
    factor_id      TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    user_id        TEXT NOT NULL REFERENCES users(user_id),
    kind           TEXT NOT NULL CHECK (kind IN ('totp','webauthn')),
    display_name   TEXT NOT NULL,
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    enrolled_at    TIMESTAMPTZ NOT NULL,
    last_used_at   TIMESTAMPTZ,
    disabled_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mfa_factors_user_active
    ON mfa_factors (org_id, user_id, enabled) WHERE disabled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mfa_factors_user_kind
    ON mfa_factors (user_id, kind, enabled);

CREATE TABLE IF NOT EXISTS totp_secrets (
    secret_id          TEXT PRIMARY KEY,
    factor_id          TEXT NOT NULL UNIQUE REFERENCES mfa_factors(factor_id),
    encrypted_secret   TEXT NOT NULL,
    last_step          BIGINT,
    created_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
    credential_id          TEXT PRIMARY KEY,
    factor_id              TEXT NOT NULL REFERENCES mfa_factors(factor_id),
    credential_id_b64      TEXT NOT NULL UNIQUE,
    public_key_cose        BYTEA NOT NULL,
    sign_count             BIGINT NOT NULL DEFAULT 0,
    transports             JSONB NOT NULL DEFAULT '[]'::jsonb,
    aaguid                 TEXT,
    attestation_format     TEXT NOT NULL,
    rp_id                  TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    last_used_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_webauthn_credentials_factor
    ON webauthn_credentials (factor_id);

CREATE TABLE IF NOT EXISTS mfa_challenges (
    challenge_id         TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    kind                 TEXT NOT NULL CHECK (kind IN ('totp','webauthn','recovery')),
    nonce                TEXT NOT NULL UNIQUE,
    expected_factor_id   TEXT,
    payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at           TIMESTAMPTZ NOT NULL,
    consumed_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mfa_challenges_pending
    ON mfa_challenges (expires_at) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mfa_challenges_user
    ON mfa_challenges (org_id, user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    code_id      TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    code_hash    TEXT NOT NULL UNIQUE,
    consumed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mfa_recovery_active
    ON mfa_recovery_codes (org_id, user_id) WHERE consumed_at IS NULL;

-- A6 also extends ``identity_policies`` to carry per-org MFA requirement
-- + step-up window. Defaults stay backwards-compatible: mfa_required
-- false (matches existing behavior) and step_up_window_seconds 300.
ALTER TABLE identity_policies
    ADD COLUMN IF NOT EXISTS mfa_required BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS step_up_window_seconds INTEGER NOT NULL DEFAULT 300;
