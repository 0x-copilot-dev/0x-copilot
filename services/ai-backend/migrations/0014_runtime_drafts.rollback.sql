-- Rollback for 0014_runtime_drafts.sql.

DROP POLICY IF EXISTS tenant_isolation ON runtime_drafts;
DROP TABLE IF EXISTS runtime_drafts;
