-- 0012_repair_planning_snapshots — D12 planning-only repair/reconciliation state.
--
-- These tables are worker-owned and deliberately contain only the redacted D12
-- snapshot contract: opaque IDs, enum facts, and safe candidate/withheld
-- decisions. They do not store artifact paths, provider bodies, effect targets,
-- prepared handles, receipts, or raw references. No queue or execution command
-- is produced from this state.

CREATE TABLE IF NOT EXISTS runtime_repair_planning_snapshots (
    org_id                      text NOT NULL,
    snapshot_id                 text NOT NULL,
    snapshot_digest             text NOT NULL,
    as_of                       timestamptz NOT NULL,
    source_complete             boolean NOT NULL,
    records_json                jsonb NOT NULL,
    cursor_after_candidate_id   text,
    completed                   boolean NOT NULL DEFAULT false,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, snapshot_id),
    CONSTRAINT runtime_repair_planning_snapshot_digest_check
        CHECK (snapshot_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_repair_planning_snapshot_id_check
        CHECK (snapshot_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$'),
    CONSTRAINT runtime_repair_planning_cursor_check
        CHECK (
            cursor_after_candidate_id IS NULL
            OR cursor_after_candidate_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$'
        )
);

CREATE TABLE IF NOT EXISTS runtime_repair_planning_outcomes (
    org_id          text NOT NULL,
    snapshot_id     text NOT NULL,
    candidate_id    text NOT NULL,
    state           text NOT NULL,
    action          text,
    reasons_json    jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, snapshot_id, candidate_id),
    FOREIGN KEY (org_id, snapshot_id)
        REFERENCES runtime_repair_planning_snapshots (org_id, snapshot_id)
        ON DELETE CASCADE,
    CONSTRAINT runtime_repair_planning_outcome_state_check
        CHECK (state IN ('candidate', 'withheld')),
    CONSTRAINT runtime_repair_planning_outcome_action_check
        CHECK (
            (state = 'candidate' AND action IS NOT NULL)
            OR (state = 'withheld' AND action IS NULL)
        ),
    CONSTRAINT runtime_repair_planning_outcome_candidate_id_check
        CHECK (candidate_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$')
);

CREATE INDEX IF NOT EXISTS idx_runtime_repair_planning_open
    ON runtime_repair_planning_snapshots (updated_at ASC, snapshot_id ASC)
    WHERE completed = false;

-- The source scan is a global keyset traversal. The A5 index predates the
-- tenant tie-breaker; retain it for legacy reads and add this exact ordering
-- so the bounded D12 scan does not sort an ever-growing unresolved set.
CREATE INDEX IF NOT EXISTS idx_runtime_effect_claims_repair_scan
    ON runtime_effect_claims (created_at ASC, org_id ASC, claim_id ASC)
    WHERE state IN ('claimed', 'indeterminate');

-- One fixed, worker-owned keyset cursor lets D12 advance through a bounded
-- unresolved-claim scan without repeatedly re-planning the oldest rows. It
-- stores timestamp + opaque IDs only, never an effect target or raw reference.
CREATE TABLE IF NOT EXISTS runtime_repair_planning_scan_state (
    source              text PRIMARY KEY,
    after_created_at    timestamptz,
    after_org_id        text,
    after_claim_id      text,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runtime_repair_planning_scan_source_check
        CHECK (source = 'effect_claims'),
    CONSTRAINT runtime_repair_planning_scan_cursor_shape_check
        CHECK (
            (after_created_at IS NULL AND after_org_id IS NULL AND after_claim_id IS NULL)
            OR
            (after_created_at IS NOT NULL AND after_org_id IS NOT NULL AND after_claim_id IS NOT NULL)
        ),
    CONSTRAINT runtime_repair_planning_scan_org_id_check
        CHECK (
            after_org_id IS NULL
            OR after_org_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
        ),
    CONSTRAINT runtime_repair_planning_scan_claim_id_check
        CHECK (
            after_claim_id IS NULL
            OR after_claim_id ~ '^[a-z0-9][a-z0-9._-]{0,127}$'
        )
);
