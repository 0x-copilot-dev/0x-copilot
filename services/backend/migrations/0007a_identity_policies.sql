-- A4 (post-merge fix): per-org identity policy table.
--
-- Closes the spec gap noted in docs/roadmap/08-a4-local-password.md §1.2:
-- "Setting identity_policy.local_password_enabled=false for an org →
-- /v1/auth/login for that org returns 404." The original A4 migration only
-- carried password_policies (rotation, complexity); this companion table
-- holds the on/off flags that gate which IdPs are accepted at all.
--
-- Reusable foundation: A6 will add ``mfa_required``, A7 will add
-- ``scim_required`` to this same table. Keeping the auth-method toggles
-- in one row per org makes the bank/gov "lock down to SAML+SCIM only"
-- posture an UPDATE of three booleans, not a schema migration.

CREATE TABLE IF NOT EXISTS identity_policies (
    org_id                  TEXT PRIMARY KEY REFERENCES organizations(org_id),
    local_password_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    -- Reserved for A6 (MFA) and A7 (SCIM); both default to OFF so the
    -- table stays additive even when later PRs introduce the columns.
    --   mfa_required    BOOLEAN NOT NULL DEFAULT FALSE
    --   scim_required   BOOLEAN NOT NULL DEFAULT FALSE
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_identity_policies_local_password
    ON identity_policies (org_id) WHERE local_password_enabled = FALSE;
