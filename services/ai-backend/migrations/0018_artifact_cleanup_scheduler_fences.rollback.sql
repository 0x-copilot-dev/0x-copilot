-- Rollback 0018_artifact_cleanup_scheduler_fences.

DROP INDEX IF EXISTS idx_runtime_artifact_cleanup_deferred_due;
DROP TABLE IF EXISTS runtime_artifact_cleanup_deferred_tenants;

ALTER TABLE runtime_artifact_cleanup_schedule_state
    DROP CONSTRAINT IF EXISTS runtime_artifact_cleanup_schedule_fence_check,
    DROP COLUMN IF EXISTS lease_fence_token;
