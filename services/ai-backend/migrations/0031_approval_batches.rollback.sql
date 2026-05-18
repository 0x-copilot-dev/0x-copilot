-- Rollback for 0031_approval_batches.

DROP POLICY IF EXISTS tenant_isolation ON runtime_approval_batch_items;
ALTER TABLE IF EXISTS runtime_approval_batch_items DISABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON runtime_approval_batches;
ALTER TABLE IF EXISTS runtime_approval_batches DISABLE ROW LEVEL SECURITY;

DROP TABLE IF EXISTS runtime_approval_batch_items;
DROP TABLE IF EXISTS runtime_approval_batches;
