-- Rollback 0037_oidc_link_binding: drop the link-binding columns.

ALTER TABLE oidc_authentications
    DROP COLUMN IF EXISTS link_user_id,
    DROP COLUMN IF EXISTS link_org_id;
