-- Rollback 0019_artifact_cleanup_execution_admission.

DROP INDEX IF EXISTS idx_runtime_artifact_cleanup_execution_retry;
DROP TABLE IF EXISTS runtime_artifact_cleanup_tenant_executions;
