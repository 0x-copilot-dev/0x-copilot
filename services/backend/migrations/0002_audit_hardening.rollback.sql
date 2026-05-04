-- Rollback for 0002_audit_hardening.

DROP TRIGGER IF EXISTS skill_audit_events_immutable ON skill_audit_events;
DROP TRIGGER IF EXISTS mcp_audit_events_immutable ON mcp_audit_events;
DROP FUNCTION IF EXISTS audit_immutable_guard();

REVOKE INSERT, SELECT ON mcp_audit_events FROM audit_writer;
REVOKE INSERT, SELECT ON skill_audit_events FROM audit_writer;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
    DROP ROLE audit_writer;
  END IF;
END
$$;

DROP INDEX IF EXISTS idx_skill_audit_events_org_seq;
DROP INDEX IF EXISTS idx_mcp_audit_events_org_seq;

ALTER TABLE skill_audit_events
  DROP COLUMN IF EXISTS key_version,
  DROP COLUMN IF EXISTS signature,
  DROP COLUMN IF EXISTS prev_hash,
  DROP COLUMN IF EXISTS seq;

ALTER TABLE mcp_audit_events
  DROP COLUMN IF EXISTS key_version,
  DROP COLUMN IF EXISTS signature,
  DROP COLUMN IF EXISTS prev_hash,
  DROP COLUMN IF EXISTS seq;
