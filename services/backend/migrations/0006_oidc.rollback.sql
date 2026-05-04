-- Rollback for 0006_oidc.
DROP TABLE IF EXISTS oidc_jwks_cache CASCADE;
DROP TABLE IF EXISTS oidc_refresh_tokens CASCADE;
DROP TABLE IF EXISTS oidc_identities CASCADE;
DROP TABLE IF EXISTS oidc_authentications CASCADE;
