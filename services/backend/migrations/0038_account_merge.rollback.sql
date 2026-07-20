-- Rollback 0038_account_merge.

DROP TRIGGER IF EXISTS identity_audit_events_immutable ON identity_audit_events;

ALTER TABLE oidc_authentications
    DROP COLUMN IF EXISTS link_confirm_merge;

ALTER TABLE users
    DROP COLUMN IF EXISTS merged_at,
    DROP COLUMN IF EXISTS absorbed_into_user_id;

DROP TABLE IF EXISTS account_merges;
