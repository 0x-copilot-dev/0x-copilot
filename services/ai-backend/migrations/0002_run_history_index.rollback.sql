-- transactional: false
-- Rollback 0002_run_history_index: drop the org+user run-history keyset index.
-- CONCURRENTLY (outside a transaction, per the `transactional: false` directive)
-- so the drop does not take a lock that blocks concurrent readers/writers.
DROP INDEX CONCURRENTLY IF EXISTS idx_agent_runs_org_user_created;
