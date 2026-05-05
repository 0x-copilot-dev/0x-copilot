-- Rollback for 0018_user_profiles_preferences.sql.
--
-- Drops both sidecar tables. CASCADE drops the indexes and the
-- tenant_isolation policies along with the tables. Audit rows that
-- referenced these writes remain in identity_audit_events (intentional —
-- the chain stays intact even after the underlying tables are dropped).

DROP TABLE IF EXISTS user_preferences CASCADE;
DROP TABLE IF EXISTS user_profiles CASCADE;
