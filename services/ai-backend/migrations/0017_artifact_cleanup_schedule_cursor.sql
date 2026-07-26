-- 0017_artifact_cleanup_schedule_cursor — one durable fair cursor + lease for
-- the opt-in worker-owned physical artifact cleanup executor.  It contains no
-- artifact, blob, reference, hold, user, or body data.

CREATE TABLE IF NOT EXISTS runtime_artifact_cleanup_schedule_state (
    source               text PRIMARY KEY,
    cursor_after_org_id  text,
    lease_owner_id       text,
    lease_expires_at     timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runtime_artifact_cleanup_schedule_source_check
        CHECK (source = 'artifact_cleanup_execution'),
    CONSTRAINT runtime_artifact_cleanup_schedule_cursor_check
        CHECK (cursor_after_org_id IS NULL OR length(cursor_after_org_id) BETWEEN 1 AND 256),
    CONSTRAINT runtime_artifact_cleanup_schedule_lease_check
        CHECK (
            (lease_owner_id IS NULL AND lease_expires_at IS NULL)
            OR
            (lease_owner_id IS NOT NULL AND lease_expires_at IS NOT NULL)
        )
);
