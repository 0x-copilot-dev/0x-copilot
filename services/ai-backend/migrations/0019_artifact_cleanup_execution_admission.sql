-- 0019_artifact_cleanup_execution_admission -- durable global admission for
-- tenant cleanup execution fences. Rows contain only worker identifiers and
-- opaque tenant ids; artifact body, blob, reference, and hold data remain in
-- the lifecycle adapters.

CREATE TABLE IF NOT EXISTS runtime_artifact_cleanup_tenant_executions (
    source                  text NOT NULL,
    execution_token         text NOT NULL,
    org_id                  text NOT NULL,
    owner_id                text NOT NULL,
    lease_fence_token       bigint NOT NULL,
    state                   text NOT NULL,
    release_failure_count   integer NOT NULL DEFAULT 0,
    retry_not_before        timestamptz NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, execution_token),
    UNIQUE (source, org_id),
    CONSTRAINT runtime_artifact_cleanup_execution_source_check
        CHECK (source = 'artifact_cleanup_execution'),
    CONSTRAINT runtime_artifact_cleanup_execution_org_check
        CHECK (length(org_id) BETWEEN 1 AND 256),
    CONSTRAINT runtime_artifact_cleanup_execution_owner_check
        CHECK (length(owner_id) BETWEEN 1 AND 256),
    CONSTRAINT runtime_artifact_cleanup_execution_fence_check
        CHECK (lease_fence_token >= 1),
    CONSTRAINT runtime_artifact_cleanup_execution_state_check
        CHECK (state IN ('active', 'quarantined', 'release_pending')),
    CONSTRAINT runtime_artifact_cleanup_execution_release_check
        CHECK (
            release_failure_count >= 0
            AND (
                (state = 'release_pending' AND retry_not_before IS NOT NULL)
                OR (state <> 'release_pending' AND retry_not_before IS NULL)
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_runtime_artifact_cleanup_execution_retry
    ON runtime_artifact_cleanup_tenant_executions (
        source, retry_not_before ASC
    )
    WHERE state = 'release_pending';
