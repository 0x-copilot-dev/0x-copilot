-- PR #43 — Promote ApprovalBatch to a first-class entity.
--
-- The LangGraph interrupt is the single source of truth for "what does the
-- graph need to resume?". An interrupt with N action_requests requires N
-- aligned decisions on resume; resuming with a 1-element decisions[] against
-- an N-element interrupt raises ``ValueError`` inside the HITL middleware and
-- the run crashes. Prior to this migration the "batch" concept lived only as
-- a substring of the per-item ``approval_id`` (``<interrupt_id>:<index>``) —
-- it had no row, no status, no lock target. This migration gives the batch a
-- typed home so resume is gated on batch completeness, not per-item.
--
-- Tables:
--   runtime_approval_batches — 1:1 with each LangGraph interrupt
--   runtime_approval_batch_items — one row per action_request
--
-- Concurrency contract:
--   ``record_item_decision_and_maybe_lock_batch`` performs item.decision +
--   batch.status flip atomically inside ``SELECT ... FOR UPDATE`` on the
--   batch row. Exactly one concurrent caller can flip ``pending -> resuming``.

CREATE TABLE IF NOT EXISTS runtime_approval_batches (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'resuming', 'resolved', 'expired')),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_runtime_approval_batches_org_run
    ON runtime_approval_batches (org_id, run_id);
CREATE INDEX IF NOT EXISTS idx_runtime_approval_batches_status_expires
    ON runtime_approval_batches (status, expires_at)
    WHERE expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime_approval_batch_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL
        REFERENCES runtime_approval_batches(id) ON DELETE CASCADE,
    item_index INTEGER NOT NULL CHECK (item_index >= 0),
    decision TEXT
        CHECK (decision IS NULL OR decision IN ('approved', 'rejected', 'forwarded')),
    UNIQUE (batch_id, item_index)
);

CREATE INDEX IF NOT EXISTS idx_runtime_approval_batch_items_batch
    ON runtime_approval_batch_items (batch_id);

-- RLS isolation. The batch table carries org_id directly; items inherit via FK
-- so we enforce isolation on the batch only and let cascading deletes / joins
-- through ``runtime_approval_batches`` handle items.
ALTER TABLE runtime_approval_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_approval_batches FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_approval_batches
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

ALTER TABLE runtime_approval_batch_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_approval_batch_items FORCE ROW LEVEL SECURITY;
-- Items are scoped through their batch's org_id via the FK; the policy
-- mirrors the batch policy by joining for safety so a direct SELECT on
-- runtime_approval_batch_items by a tenant connection still enforces scope.
CREATE POLICY tenant_isolation ON runtime_approval_batch_items
    USING (
        EXISTS (
            SELECT 1
            FROM runtime_approval_batches b
            WHERE b.id = runtime_approval_batch_items.batch_id
              AND b.org_id = current_setting('app.current_org_id', true)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM runtime_approval_batches b
            WHERE b.id = runtime_approval_batch_items.batch_id
              AND b.org_id = current_setting('app.current_org_id', true)
        )
    );
