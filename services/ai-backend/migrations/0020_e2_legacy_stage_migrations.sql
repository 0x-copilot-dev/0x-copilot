-- 0020_e2_legacy_stage_migrations — E2 D5 source-fenced pending-stage mappings.
--
-- Stores only redacted identifiers, source digest, and a non-executing migration
-- outcome. It never stores proposal bytes, arguments, old approval identity, queue
-- payloads, or executable commands.

CREATE TABLE IF NOT EXISTS runtime_e2_legacy_stage_migrations (
    org_id              text NOT NULL,
    migration_id        text NOT NULL,
    run_id              text NOT NULL,
    legacy_stage_id     text NOT NULL,
    source_digest       text NOT NULL,
    outcome             text NOT NULL,
    canonical_stage_id  text,
    queue_cancelled     boolean NOT NULL DEFAULT false,
    reconciler_frozen   boolean NOT NULL DEFAULT false,
    revision            bigint NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, migration_id, run_id, legacy_stage_id),
    CONSTRAINT runtime_e2_legacy_stage_migration_source_digest_check
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_e2_legacy_stage_migration_outcome_check
        CHECK (outcome IN ('compatibility_only', 'canonical_held', 'frozen_reconcile', 'quarantined')),
    CONSTRAINT runtime_e2_legacy_stage_migration_facts_check
        CHECK (
            (outcome = 'canonical_held' AND canonical_stage_id IS NOT NULL AND NOT reconciler_frozen)
            OR (outcome = 'frozen_reconcile' AND canonical_stage_id IS NULL AND reconciler_frozen)
            OR (outcome IN ('compatibility_only', 'quarantined') AND canonical_stage_id IS NULL AND NOT reconciler_frozen)
        ),
    CONSTRAINT runtime_e2_legacy_stage_migration_revision_check CHECK (revision >= 0)
);

ALTER TABLE runtime_e2_legacy_stage_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_e2_legacy_stage_migrations FORCE ROW LEVEL SECURITY;

CREATE POLICY e2_legacy_stage_migration_worker_only
    ON runtime_e2_legacy_stage_migrations
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');

GRANT SELECT, INSERT ON runtime_e2_legacy_stage_migrations TO enterprise_app;
