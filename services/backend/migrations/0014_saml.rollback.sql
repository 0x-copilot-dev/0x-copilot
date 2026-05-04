-- Rollback for 0014_saml.sql.
DROP INDEX IF EXISTS idx_saml_identity_user;
DROP INDEX IF EXISTS idx_saml_identity_nameid;
DROP TABLE IF EXISTS saml_identities;

DROP INDEX IF EXISTS idx_saml_pending;
DROP INDEX IF EXISTS idx_saml_request;
DROP INDEX IF EXISTS idx_saml_assertion_replay;
DROP TABLE IF EXISTS saml_authentications;
