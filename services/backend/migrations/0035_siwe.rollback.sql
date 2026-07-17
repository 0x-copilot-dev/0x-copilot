-- Rollback for 0035_siwe.sql.
--
-- Narrowing a CHECK requires the surviving rows to satisfy it first:
-- 'siwe' members fold back to 'local' and 'siwe' login attempts are
-- retagged 'local' (both retain their audit trail rows; only the tag
-- narrows). Then the SIWE tables drop.

UPDATE organization_members SET source = 'local' WHERE source = 'siwe';
ALTER TABLE organization_members DROP CONSTRAINT IF EXISTS organization_members_source_check;
ALTER TABLE organization_members ADD CONSTRAINT organization_members_source_check
    CHECK (source IN ('local','oidc','saml','scim','bootstrap','invite'));

UPDATE login_attempts SET auth_kind = 'local' WHERE auth_kind = 'siwe';
ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_auth_kind_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_auth_kind_check
    CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key','magic_link'));

DROP TABLE IF EXISTS wallet_identities CASCADE;
DROP TABLE IF EXISTS siwe_nonces CASCADE;
