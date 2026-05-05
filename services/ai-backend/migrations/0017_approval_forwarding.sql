-- PR 1.4 — Two-stage approvals (forward chain).
--
-- Forwarding is a finite-state addition to runtime_approval_requests, NOT a
-- harness change. The LangChain HumanInTheLoopMiddleware and the LangGraph
-- interrupt/resume contract stay byte-identical: the graph experiences exactly
-- one resume per side-effecting tool call, on the leaf approver's decision.
--
-- This migration adds:
--   - 'forwarded' as a terminal status for the parent approval row
--   - chain_parent_approval_id to link a child approval to its parent
--   - forwarded_to_user_id / forwarded_at / forwarded_decided_at as
--     bookkeeping for the forward link
--   - a CHECK preventing self-parent
--   - an index for the "show me the chain for this run" read path

ALTER TABLE runtime_approval_requests
    ADD COLUMN IF NOT EXISTS chain_parent_approval_id TEXT
        REFERENCES runtime_approval_requests(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS forwarded_to_user_id TEXT,
    ADD COLUMN IF NOT EXISTS forwarded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS forwarded_decided_at TIMESTAMPTZ;

ALTER TABLE runtime_approval_requests
    DROP CONSTRAINT IF EXISTS runtime_approval_requests_no_self_parent;
ALTER TABLE runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_no_self_parent
        CHECK (
            chain_parent_approval_id IS NULL
            OR chain_parent_approval_id <> id
        );

-- Replace the status CHECK to allow 'forwarded' as a terminal parent state.
-- The runtime worker discriminates on status before resuming: only 'approved'
-- and 'rejected' produce Command(resume=...) calls; 'forwarded' is terminal
-- bookkeeping that leaves the run paused on the (newly inserted) child row.
ALTER TABLE runtime_approval_requests
    DROP CONSTRAINT IF EXISTS runtime_approval_requests_status_check;
ALTER TABLE runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_status_check
        CHECK (status IN ('pending', 'approved', 'rejected', 'forwarded'));

CREATE INDEX IF NOT EXISTS idx_runtime_approval_requests_chain_parent
    ON runtime_approval_requests (run_id, chain_parent_approval_id)
    WHERE chain_parent_approval_id IS NOT NULL;
