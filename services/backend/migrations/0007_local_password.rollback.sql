-- Rollback for 0007_local_password.
DROP TABLE IF EXISTS password_reset_tokens CASCADE;
DROP TABLE IF EXISTS password_policies CASCADE;
DROP TABLE IF EXISTS local_credentials CASCADE;
