-- Rollback 0040_identity_principal_edges.

DROP INDEX IF EXISTS idx_saml_identities_principal;
DROP INDEX IF EXISTS idx_oidc_identities_principal;
DROP INDEX IF EXISTS idx_wallet_identities_principal;

ALTER TABLE saml_identities DROP COLUMN IF EXISTS principal_id;
ALTER TABLE oidc_identities DROP COLUMN IF EXISTS principal_id;
ALTER TABLE wallet_identities DROP COLUMN IF EXISTS principal_id;
