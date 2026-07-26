-- 0015_e2_legacy_migration_checkpoints — E2 evidence-gated legacy import.
--
-- This table deliberately persists only a tenant-scoped source digest, a
-- resumable opaque cursor, safe status, and a report digest.  It contains no
-- legacy draft bodies, filesystem paths, target arguments, approval records,
-- or executable work.  A worker may only advance after a whole draft history
-- has been verified or quarantined.

CREATE TABLE IF NOT EXISTS runtime_e2_legacy_migrations (
    org_id          text NOT NULL,
    migration_id    text NOT NULL,
    source_digest   text NOT NULL,
    after_draft_id  text,
    status          text NOT NULL,
    report_digest   text,
    revision        bigint NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, migration_id),
    CONSTRAINT runtime_e2_legacy_migration_id_check
        CHECK (migration_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
    CONSTRAINT runtime_e2_legacy_migration_source_digest_check
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_e2_legacy_migration_after_draft_check
        CHECK (
            after_draft_id IS NULL
            OR after_draft_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'
        ),
    CONSTRAINT runtime_e2_legacy_migration_status_check
        CHECK (status IN ('running', 'completed', 'blocked', 'audit_pending')),
    CONSTRAINT runtime_e2_legacy_migration_report_digest_check
        CHECK (report_digest IS NULL OR report_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_e2_legacy_migration_revision_check
        CHECK (revision >= 0)
);

CREATE INDEX IF NOT EXISTS idx_runtime_e2_legacy_migrations_open
    ON runtime_e2_legacy_migrations (updated_at ASC, migration_id ASC)
    WHERE status IN ('running', 'audit_pending');

ALTER TABLE runtime_e2_legacy_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_e2_legacy_migrations FORCE ROW LEVEL SECURITY;

-- The internal control-plane handler opens a worker-role connection after its
-- service-token check.  No tenant-facing API has access to this state.
CREATE POLICY e2_legacy_migration_worker_only
    ON runtime_e2_legacy_migrations
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');

GRANT SELECT, INSERT, UPDATE
    ON runtime_e2_legacy_migrations TO enterprise_app;
