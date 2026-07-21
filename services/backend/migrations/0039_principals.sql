-- 0039_principals — principal/tenant separation, STAGE 1 of 3 (EXPAND). ADR 0001.
--
-- Today identity and tenancy are the SAME row: each self-signup provisions its
-- own personal org + one `users` row, and every auth identity (wallet/OIDC)
-- points straight at that (org_id, user_id). A human who signs in with two
-- methods becomes two accounts — the reason the account-merge engine has to
-- exist. This migration introduces the `principal` (one row per human) that
-- auth identities and memberships will eventually hang off, so that linking a
-- second method becomes an insert and merge degrades to a legacy-repair tool.
--
-- STAGE 1 is deliberately inert at the read layer: it adds the table + a
-- nullable `users.principal_id`, backfills 1:1, and changes NO resolver. The
-- application dual-writes principal_id on new users from here on (expand/
-- parallel-change), so by the time Stage 2 points sign-in resolution at the
-- principal, the column is reliably populated for every row. Fully reversible
-- (see 0039_principals.rollback.sql).

CREATE TABLE principals (
    principal_id   TEXT PRIMARY KEY,
    display_name   TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Principal-layer merge lineage, mirroring users.absorbed_into_user_id.
    -- Populated for pre-migration merges by the backfill below; the future
    -- "merge reconciles principals" stage stamps it going forward. A human
    -- whose account was absorbed keeps a (dead) principal pointing at the
    -- survivor's, so history stays traceable (NFR-6 lineage doctrine).
    absorbed_into_principal_id TEXT REFERENCES principals (principal_id),
    merged_at      TIMESTAMPTZ
);

-- No org_id: a principal is ABOVE orgs (the whole point). It is therefore not
-- an RLS tenant table and not part of the account-merge tenant registry; the
-- merge schema-consistency guard only classifies org-scoped tables.

ALTER TABLE users
    ADD COLUMN principal_id TEXT REFERENCES principals (principal_id);

-- Backfill (idempotent): one principal per existing user. Per-user (not
-- per-survivor) so the FK is satisfied regardless of merge-chain shape; the
-- principal-level lineage is then stamped from the user-level lineage.
INSERT INTO principals (principal_id, display_name, created_at, updated_at)
SELECT 'prn_' || user_id, display_name, created_at, created_at
FROM users
ON CONFLICT (principal_id) DO NOTHING;

UPDATE users
SET principal_id = 'prn_' || user_id
WHERE principal_id IS NULL;

UPDATE principals p
SET absorbed_into_principal_id = 'prn_' || u.absorbed_into_user_id,
    merged_at = u.merged_at
FROM users u
WHERE p.principal_id = 'prn_' || u.user_id
  AND u.absorbed_into_user_id IS NOT NULL;

CREATE INDEX idx_users_principal ON users (principal_id);
