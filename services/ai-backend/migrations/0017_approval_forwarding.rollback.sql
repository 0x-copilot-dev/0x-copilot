-- Rollback for PR 1.4 — Two-stage approvals.
--
-- Order matters: drop FK + check constraints before dropping the columns
-- they reference, restore the original status CHECK, then drop the index.

ALTER TABLE runtime_approval_requests
    DROP CONSTRAINT IF EXISTS runtime_approval_requests_no_self_parent;
ALTER TABLE runtime_approval_requests
    DROP CONSTRAINT IF EXISTS runtime_approval_requests_status_check;
ALTER TABLE runtime_approval_requests
    ADD CONSTRAINT runtime_approval_requests_status_check
        CHECK (status IN ('pending', 'approved', 'rejected'));

DROP INDEX IF EXISTS idx_runtime_approval_requests_chain_parent;

ALTER TABLE runtime_approval_requests
    DROP COLUMN IF EXISTS forwarded_decided_at,
    DROP COLUMN IF EXISTS forwarded_at,
    DROP COLUMN IF EXISTS forwarded_to_user_id,
    DROP COLUMN IF EXISTS chain_parent_approval_id;
