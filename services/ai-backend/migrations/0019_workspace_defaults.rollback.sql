-- Rollback PR 1.6 workspace defaults.
DROP POLICY IF EXISTS tenant_isolation ON workspace_defaults;
ALTER TABLE IF EXISTS workspace_defaults DISABLE ROW LEVEL SECURITY;
DROP TABLE IF EXISTS workspace_defaults;
