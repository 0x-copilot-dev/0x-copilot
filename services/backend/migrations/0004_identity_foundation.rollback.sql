-- Rollback for 0004_identity_foundation. CASCADE bypasses FK ordering.
-- The CITEXT extension is kept (other future migrations may rely on it).
DROP TABLE IF EXISTS login_attempts CASCADE;
DROP TABLE IF EXISTS identity_audit_events CASCADE;
DROP TABLE IF EXISTS auth_providers CASCADE;
DROP TABLE IF EXISTS role_assignments CASCADE;
DROP TABLE IF EXISTS roles CASCADE;
DROP TABLE IF EXISTS organization_members CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
