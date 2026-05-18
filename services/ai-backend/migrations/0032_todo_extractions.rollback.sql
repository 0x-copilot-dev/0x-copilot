-- Rollback for 0031_todo_extractions.sql.
--
-- Drops the table, its indexes, and its RLS policy in reverse order.
-- This is destructive — any pending/accepted/rejected proposals are
-- lost. Reapply 0031 to recreate the schema; user-accepted todos in
-- the public ``todos`` table (owned by backend service / P3-A1) are
-- unaffected.

DROP POLICY IF EXISTS tenant_isolation ON todo_extractions;
DROP INDEX  IF EXISTS ix_todo_extractions_org_run;
DROP INDEX  IF EXISTS ix_todo_extractions_owner_pending;
DROP TABLE  IF EXISTS todo_extractions;
