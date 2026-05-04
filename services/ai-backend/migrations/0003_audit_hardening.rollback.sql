-- Rollback for 0003_audit_hardening.
DROP TRIGGER IF EXISTS runtime_audit_log_immutable ON runtime_audit_log;
DROP FUNCTION IF EXISTS runtime_audit_log_immutable_guard();
REVOKE INSERT, SELECT ON runtime_audit_log FROM audit_writer;

DROP INDEX IF EXISTS idx_runtime_audit_log_org_seq;

ALTER TABLE runtime_audit_log
    DROP COLUMN IF EXISTS key_version,
    DROP COLUMN IF EXISTS signature,
    DROP COLUMN IF EXISTS prev_hash,
    DROP COLUMN IF EXISTS seq;
