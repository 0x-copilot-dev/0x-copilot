-- Rollback for 0015_scim.sql.
ALTER TABLE identity_policies DROP COLUMN IF EXISTS scim_required;

DROP INDEX IF EXISTS idx_users_scim_external_id;
ALTER TABLE users DROP COLUMN IF EXISTS scim_external_id;

DROP INDEX IF EXISTS idx_scim_group_member_user;
DROP INDEX IF EXISTS idx_scim_group_member_active;
DROP TABLE IF EXISTS scim_group_members;

DROP INDEX IF EXISTS idx_scim_groups_role;
DROP INDEX IF EXISTS idx_scim_groups_name;
DROP TABLE IF EXISTS scim_groups;

DROP INDEX IF EXISTS idx_scim_external_group;
DROP INDEX IF EXISTS idx_scim_external_user;
DROP INDEX IF EXISTS idx_scim_external_id;
DROP TABLE IF EXISTS scim_external_ids;

DROP INDEX IF EXISTS idx_scim_token_org;
DROP INDEX IF EXISTS idx_scim_token_hash;
DROP TABLE IF EXISTS scim_tokens;
