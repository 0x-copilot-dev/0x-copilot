-- 0018_artifact_cleanup_scheduler_fences — durable deferred retries plus a
-- renewable generation fence for the opt-in physical-artifact cleanup worker.
-- This state retains no artifact body, blob key, reference, legal hold, or
-- product-visible tenant data beyond the worker-only opaque org identifier.

ALTER TABLE runtime_artifact_cleanup_schedule_state
    ADD COLUMN IF NOT EXISTS lease_fence_token bigint NOT NULL DEFAULT 0,
    ADD CONSTRAINT runtime_artifact_cleanup_schedule_fence_check
        CHECK (lease_fence_token >= 0);

CREATE TABLE IF NOT EXISTS runtime_artifact_cleanup_deferred_tenants (
    source              text NOT NULL,
    org_id              text NOT NULL,
    failure_count       integer NOT NULL,
    retry_not_before    timestamptz NOT NULL,
    last_failed_at      timestamptz NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, org_id),
    CONSTRAINT runtime_artifact_cleanup_deferred_source_check
        CHECK (source = 'artifact_cleanup_execution'),
    CONSTRAINT runtime_artifact_cleanup_deferred_org_check
        CHECK (length(org_id) BETWEEN 1 AND 256),
    CONSTRAINT runtime_artifact_cleanup_deferred_failure_check
        CHECK (failure_count >= 1),
    CONSTRAINT runtime_artifact_cleanup_deferred_time_check
        CHECK (retry_not_before >= last_failed_at)
);

CREATE INDEX IF NOT EXISTS idx_runtime_artifact_cleanup_deferred_due
    ON runtime_artifact_cleanup_deferred_tenants (source, retry_not_before ASC);
