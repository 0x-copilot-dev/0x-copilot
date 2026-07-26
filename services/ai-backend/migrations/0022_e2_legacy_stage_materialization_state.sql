-- 0022_e2_legacy_stage_materialization_state
--
-- Harden E2 D5: a reservation is a recoverable state machine, and the
-- canonical ``effect.staged`` append consumes the RESERVED row in its own
-- transaction.  There is no executable command in either table.

ALTER TABLE runtime_e2_legacy_stage_reservations
    ADD COLUMN IF NOT EXISTS canonical_stage_id text,
    ADD COLUMN IF NOT EXISTS materialization_state text NOT NULL DEFAULT 'reserved',
    ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

UPDATE runtime_e2_legacy_stage_reservations
   SET canonical_stage_id = 'stg_legacy_unrecoverable',
       materialization_state = 'quarantined'
 WHERE canonical_stage_id IS NULL;

ALTER TABLE runtime_e2_legacy_stage_reservations
    ALTER COLUMN canonical_stage_id SET NOT NULL,
    ADD CONSTRAINT runtime_e2_legacy_stage_reservation_state_check
        CHECK (materialization_state = ANY (ARRAY[
            'reserved'::text, 'staged'::text, 'mapped'::text,
            'quarantined'::text, 'released'::text
        ])),
    ADD CONSTRAINT runtime_e2_legacy_stage_reservation_revision_check
        CHECK (revision >= 0);

GRANT UPDATE ON runtime_e2_legacy_stage_reservations TO enterprise_app;

-- Claimed and indeterminate old commands never return to a worker.  This
-- checkpoint records each operator reassessment and explicit terminal action.
CREATE TABLE IF NOT EXISTS runtime_e2_legacy_stage_reconciliations (
    org_id              text NOT NULL,
    run_id              text NOT NULL,
    legacy_stage_id     text NOT NULL,
    source_digest       text NOT NULL,
    status              text NOT NULL,
    checkpoint_revision integer NOT NULL DEFAULT 0,
    reassessed_at       timestamptz NOT NULL DEFAULT now(),
    terminal_at         timestamptz NULL,
    operator_ref        text NOT NULL,
    migration_job_id    text NOT NULL,
    PRIMARY KEY (org_id, run_id, legacy_stage_id),
    CONSTRAINT runtime_e2_legacy_stage_reconciliation_status_check
        CHECK (status = ANY (ARRAY[
            'frozen'::text, 'released'::text, 'quarantined'::text
        ])),
    CONSTRAINT runtime_e2_legacy_stage_reconciliation_digest_check
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_e2_legacy_stage_reconciliation_revision_check
        CHECK (checkpoint_revision >= 0)
);

ALTER TABLE runtime_e2_legacy_stage_reconciliations ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_e2_legacy_stage_reconciliations FORCE ROW LEVEL SECURITY;
CREATE POLICY e2_legacy_stage_reconciliation_worker_only
    ON runtime_e2_legacy_stage_reconciliations
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');
GRANT SELECT, INSERT, UPDATE ON runtime_e2_legacy_stage_reconciliations TO enterprise_app;

-- Full-fact import evidence is intentionally ref/digest-only.  Rows without
-- this proof stay quarantined; no resolver fabricates missing bytes or args.
CREATE TABLE IF NOT EXISTS runtime_e2_legacy_stage_evidence (
    org_id              text NOT NULL,
    run_id              text NOT NULL,
    legacy_stage_id     text NOT NULL,
    source_digest       text NOT NULL,
    candidate_json      jsonb NOT NULL,
    proof_digest        text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, run_id, legacy_stage_id, source_digest),
    CONSTRAINT runtime_e2_legacy_stage_evidence_source_digest_check
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_e2_legacy_stage_evidence_proof_digest_check
        CHECK (proof_digest ~ '^[0-9a-f]{64}$')
);

ALTER TABLE runtime_e2_legacy_stage_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_e2_legacy_stage_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY e2_legacy_stage_evidence_worker_only
    ON runtime_e2_legacy_stage_evidence
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');
GRANT SELECT, INSERT ON runtime_e2_legacy_stage_evidence TO enterprise_app;
