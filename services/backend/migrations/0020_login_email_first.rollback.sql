-- Rollback for 0020_login_email_first.sql.
-- Restores the prior login_attempts CHECK constraints and drops the two
-- new tables. magic_link_tokens rows are operational state with 15-minute
-- TTL; auth_provider_domains rows can be re-claimed by admins after
-- rolling forward again.

DROP TABLE IF EXISTS magic_link_tokens;
DROP TABLE IF EXISTS auth_provider_domains;

ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_outcome_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_outcome_check
    CHECK (outcome IN (
        'success','bad_password','unknown_user','locked_out','mfa_failed','provider_rejected'
    ));

ALTER TABLE login_attempts DROP CONSTRAINT IF EXISTS login_attempts_auth_kind_check;
ALTER TABLE login_attempts ADD  CONSTRAINT login_attempts_auth_kind_check
    CHECK (auth_kind IN ('local','oidc','saml','mfa','scim_token','api_key'));
