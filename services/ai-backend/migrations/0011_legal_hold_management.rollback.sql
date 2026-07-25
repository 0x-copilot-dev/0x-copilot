-- Rollback 0011_legal_hold_management.

DROP INDEX IF EXISTS idx_runtime_legal_holds_org_active_created;
DROP INDEX IF EXISTS idx_runtime_legal_holds_release_idempotency;
DROP INDEX IF EXISTS idx_runtime_legal_holds_create_idempotency;

ALTER TABLE runtime_legal_holds
    DROP CONSTRAINT IF EXISTS runtime_legal_holds_release_idempotency_shape_check,
    DROP CONSTRAINT IF EXISTS runtime_legal_holds_create_idempotency_shape_check,
    DROP CONSTRAINT IF EXISTS runtime_legal_holds_scope_owner_check,
    DROP CONSTRAINT IF EXISTS runtime_legal_holds_revision_check,
    DROP CONSTRAINT IF EXISTS runtime_legal_holds_reason_code_check,
    DROP COLUMN IF EXISTS release_request_digest,
    DROP COLUMN IF EXISTS release_idempotency_key,
    DROP COLUMN IF EXISTS create_request_digest,
    DROP COLUMN IF EXISTS create_idempotency_key,
    DROP COLUMN IF EXISTS revision,
    DROP COLUMN IF EXISTS reason_code;
