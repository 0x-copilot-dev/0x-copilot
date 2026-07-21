-- 0040_identity_principal_edges — principal/tenant separation, STAGE 2a (EXPAND).
-- ADR 0001, follows 0039 (principals table + users.principal_id).
--
-- Point the auth-identity EDGE tables at the principal so Stage 2b can resolve
-- sign-in as identity → principal → (org, user). Like 0039 this is inert at the
-- read layer: a nullable column, backfilled 1:1 from the owning user's
-- principal, plus an index the resolver will use. The store dual-writes the
-- column on new rows, so nothing lands NULL going forward. Independently
-- revertible (see rollback).
--
-- Scope is the three durable identity EDGES only: wallet_identities,
-- oidc_identities, saml_identities — each carries (org_id, user_id), so the
-- backfill is a straight join to users. `oidc_authentications` /
-- `saml_authentications` are TRANSIENT OAuth/SAML flow-state rows with no
-- stable user binding for a plain sign-in (only the link flow sets
-- link_user_id); their principal binding is a Stage 2b concern
-- ("sign-in becomes link"), deliberately not touched here.

ALTER TABLE wallet_identities
    ADD COLUMN principal_id TEXT REFERENCES principals (principal_id);
ALTER TABLE oidc_identities
    ADD COLUMN principal_id TEXT REFERENCES principals (principal_id);
ALTER TABLE saml_identities
    ADD COLUMN principal_id TEXT REFERENCES principals (principal_id);

-- Backfill 1:1 from the owning user (idempotent — only fills NULLs). Every
-- user has a principal after 0039, so this populates every existing edge.
UPDATE wallet_identities e
SET principal_id = u.principal_id
FROM users u
WHERE e.org_id = u.org_id AND e.user_id = u.user_id AND e.principal_id IS NULL;

UPDATE oidc_identities e
SET principal_id = u.principal_id
FROM users u
WHERE e.org_id = u.org_id AND e.user_id = u.user_id AND e.principal_id IS NULL;

UPDATE saml_identities e
SET principal_id = u.principal_id
FROM users u
WHERE e.org_id = u.org_id AND e.user_id = u.user_id AND e.principal_id IS NULL;

CREATE INDEX idx_wallet_identities_principal
    ON wallet_identities (principal_id);
CREATE INDEX idx_oidc_identities_principal
    ON oidc_identities (principal_id);
CREATE INDEX idx_saml_identities_principal
    ON saml_identities (principal_id);
