-- Rollback for PR 1.4.1 chain_depth column.

ALTER TABLE runtime_approval_requests
    DROP COLUMN IF EXISTS chain_depth;
