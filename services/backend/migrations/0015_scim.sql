-- A7: SCIM 2.0 user/group provisioning state.
--
-- Four tables + ALTER on users:
--
--   scim_tokens          — per-org bearer credentials. token_hash = sha256
--                          of the plaintext returned at mint; never the
--                          plaintext itself. token_prefix is the first 8
--                          chars of the plaintext so an admin listing can
--                          identify a token without re-revealing it
--                          (GitHub PAT pattern). Mint never auto-revokes
--                          the previous token — admins handle rotation
--                          explicitly to allow zero-IdP-downtime cutover.
--   scim_external_ids    — IdP's stable external_id ↔ local user_id /
--                          group_id mapping. One CHECK constraint enforces
--                          XOR — a row maps either a user or a group, not
--                          both. UNIQUE (provider_id, external_id) is the
--                          deduplication key the IdP relies on.
--   scim_groups          — display_name + optional mapped_role_id. Soft
--                          delete via deleted_at; partial unique index
--                          allows re-creation with the same display_name
--                          after delete.
--   scim_group_members   — append-only with removed_at; partial unique
--                          index on (group_id, user_id) WHERE
--                          removed_at IS NULL keeps "at most one active
--                          membership" without preventing re-add later.
--
-- Plus ALTER users ADD scim_external_id + partial unique index so the
-- IdP can store its native external id directly on the user row (the
-- mapping table covers re-link cases; this column covers the common
-- single-IdP case).
--
-- RLS deferred — same pattern as 0014_saml.sql / 0011_mfa.sql.

CREATE TABLE IF NOT EXISTS scim_tokens (
    token_id            TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    provider_id         TEXT NOT NULL REFERENCES auth_providers(provider_id),
    token_hash          TEXT NOT NULL,
    token_prefix        TEXT NOT NULL,
    created_by_user_id  TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    last_used_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scim_token_hash
    ON scim_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_scim_token_org
    ON scim_tokens (org_id, revoked_at);

CREATE TABLE IF NOT EXISTS scim_external_ids (
    mapping_id   TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    user_id      TEXT REFERENCES users(user_id),
    group_id     TEXT,
    provider_id  TEXT NOT NULL REFERENCES auth_providers(provider_id),
    external_id  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    CONSTRAINT scim_external_ids_user_xor_group CHECK (
        (user_id IS NOT NULL) <> (group_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scim_external_id
    ON scim_external_ids (provider_id, external_id);
CREATE INDEX IF NOT EXISTS idx_scim_external_user
    ON scim_external_ids (user_id);
CREATE INDEX IF NOT EXISTS idx_scim_external_group
    ON scim_external_ids (group_id);

CREATE TABLE IF NOT EXISTS scim_groups (
    group_id        TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    external_id     TEXT,
    mapped_role_id  TEXT REFERENCES roles(role_id),
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    deleted_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scim_groups_name
    ON scim_groups (org_id, display_name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scim_groups_role
    ON scim_groups (mapped_role_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS scim_group_members (
    membership_id  TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    group_id       TEXT NOT NULL REFERENCES scim_groups(group_id),
    user_id        TEXT NOT NULL REFERENCES users(user_id),
    added_at       TIMESTAMPTZ NOT NULL,
    removed_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scim_group_member_active
    ON scim_group_members (group_id, user_id) WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scim_group_member_user
    ON scim_group_members (org_id, user_id) WHERE removed_at IS NULL;

ALTER TABLE users ADD COLUMN IF NOT EXISTS scim_external_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_scim_external_id
    ON users (org_id, scim_external_id) WHERE scim_external_id IS NOT NULL;

-- Bank/gov mode toggle: when on, local password is disabled AND OIDC JIT
-- provisioning is rejected — only SCIM-provisioned users can exist.
-- Default false keeps the migration backwards-compatible.
ALTER TABLE identity_policies
    ADD COLUMN IF NOT EXISTS scim_required BOOLEAN NOT NULL DEFAULT FALSE;
