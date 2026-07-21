-- Rollback 0039_principals.

DROP INDEX IF EXISTS idx_users_principal;

ALTER TABLE users
    DROP COLUMN IF EXISTS principal_id;

DROP TABLE IF EXISTS principals;
