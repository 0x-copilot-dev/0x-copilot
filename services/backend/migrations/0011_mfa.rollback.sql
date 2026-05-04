ALTER TABLE identity_policies
    DROP COLUMN IF EXISTS step_up_window_seconds,
    DROP COLUMN IF EXISTS mfa_required;
DROP INDEX IF EXISTS idx_mfa_recovery_active;
DROP TABLE IF EXISTS mfa_recovery_codes;
DROP INDEX IF EXISTS idx_mfa_challenges_user;
DROP INDEX IF EXISTS idx_mfa_challenges_pending;
DROP TABLE IF EXISTS mfa_challenges;
DROP INDEX IF EXISTS idx_webauthn_credentials_factor;
DROP TABLE IF EXISTS webauthn_credentials;
DROP TABLE IF EXISTS totp_secrets;
DROP INDEX IF EXISTS idx_mfa_factors_user_kind;
DROP INDEX IF EXISTS idx_mfa_factors_user_active;
DROP TABLE IF EXISTS mfa_factors;
