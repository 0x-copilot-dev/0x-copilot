-- 0009_effect_claim_store — A5 durable pre-execution claims.
--
-- The effect coordinator inserts a claim before invoking an external executor.
-- The composite primary key makes an idempotency replay observable while a
-- changed request under the same key remains a fail-closed domain conflict.
-- Rows hold only bounded metadata and opaque artifact/receipt references; no
-- proposal body, credential, provider response, or filesystem path is stored.
--
-- This is worker-owned operator state, like 0006_stage_commit_ledger: only a
-- worker-role connection reaches it, including cross-tenant reconciliation
-- sweeps. It deliberately has no tenant HTTP access path.

CREATE TABLE IF NOT EXISTS runtime_effect_claims (
    org_id              text NOT NULL,
    run_id              text NOT NULL,
    stage_id            text NOT NULL,
    revision            integer NOT NULL,
    claim_id            text NOT NULL,
    idempotency_key     text NOT NULL,
    executor            text NOT NULL,
    proposal_digest     text NOT NULL,
    target_digest       text NOT NULL,
    state               text NOT NULL,
    attempt             integer NOT NULL DEFAULT 1,
    prepared_ref        text,
    receipt_ref         text,
    outcome             text,
    result_digest       text,
    safe_message        text,
    target_ref          text NOT NULL,
    proposal_ref        text NOT NULL,
    proposal_content_ref text NOT NULL,
    actor               text NOT NULL,
    decision_ledger_id  text NOT NULL,
    created_at          timestamptz NOT NULL,
    updated_at          timestamptz NOT NULL,
    PRIMARY KEY (org_id, executor, idempotency_key),
    UNIQUE (org_id, claim_id),
    CONSTRAINT runtime_effect_claims_revision_positive CHECK (revision > 0),
    CONSTRAINT runtime_effect_claims_attempt_positive CHECK (attempt > 0),
    CONSTRAINT runtime_effect_claims_executor_check CHECK (
        executor IN ('mcp', 'workspace', 'browser', 'sandbox', 'builtin')
    ),
    CONSTRAINT runtime_effect_claims_state_check CHECK (
        state IN ('claimed', 'completed', 'indeterminate', 'cancelled')
    ),
    CONSTRAINT runtime_effect_claims_outcome_check CHECK (
        outcome IS NULL OR outcome IN (
            'applied', 'partial', 'failed', 'cancelled', 'indeterminate',
            'already_applied', 'precondition_drift'
        )
    ),
    CONSTRAINT runtime_effect_claims_actor_check CHECK (
        actor IN ('user', 'policy', 'system')
    ),
    CONSTRAINT runtime_effect_claims_proposal_digest_check CHECK (
        proposal_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT runtime_effect_claims_target_digest_check CHECK (
        target_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT runtime_effect_claims_result_digest_check CHECK (
        result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT runtime_effect_claims_state_outcome_check CHECK (
        (state = 'claimed' AND outcome IS NULL)
        OR (state = 'completed' AND outcome IS NOT NULL AND outcome <> 'indeterminate')
        OR (state = 'indeterminate' AND outcome = 'indeterminate')
        OR (state = 'cancelled' AND outcome = 'cancelled')
    )
);

-- Reconciliation is a bounded oldest-first scan. The global index supports a
-- fleet sweep; the tenant-scoped one keeps a per-org recovery request bounded.
CREATE INDEX IF NOT EXISTS idx_runtime_effect_claims_incomplete
    ON runtime_effect_claims (created_at ASC, claim_id ASC)
    WHERE state IN ('claimed', 'indeterminate');
CREATE INDEX IF NOT EXISTS idx_runtime_effect_claims_org_incomplete
    ON runtime_effect_claims (org_id, created_at ASC, claim_id ASC)
    WHERE state IN ('claimed', 'indeterminate');
