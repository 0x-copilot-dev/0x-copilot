-- Phase 3: tamper-evident audit log.
--
-- Each row stores an HMAC chain signature. Per-(table, org_id) chain.
-- Chain fields are nullable: rows written before this migration will
-- have NULL signature and are flagged as invalid by the verifier, which
-- is the desired behavior (customers can see exactly when chain
-- integrity began).
ALTER TABLE mcp_audit_events
  ADD COLUMN IF NOT EXISTS seq BIGINT,
  ADD COLUMN IF NOT EXISTS prev_hash BYTEA,
  ADD COLUMN IF NOT EXISTS signature BYTEA,
  ADD COLUMN IF NOT EXISTS key_version SMALLINT;

CREATE INDEX IF NOT EXISTS idx_mcp_audit_events_org_seq
  ON mcp_audit_events (org_id, seq);

ALTER TABLE skill_audit_events
  ADD COLUMN IF NOT EXISTS seq BIGINT,
  ADD COLUMN IF NOT EXISTS prev_hash BYTEA,
  ADD COLUMN IF NOT EXISTS signature BYTEA,
  ADD COLUMN IF NOT EXISTS key_version SMALLINT;

CREATE INDEX IF NOT EXISTS idx_skill_audit_events_org_seq
  ON skill_audit_events (org_id, seq);

-- Append-only role: holds INSERT/SELECT only on audit tables, no
-- UPDATE/DELETE grant. Application connections that emit audit events
-- should connect as this role so a compromised app process cannot
-- mutate history. Idempotent via DO block.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer'
  ) THEN
    CREATE ROLE audit_writer NOLOGIN;
  END IF;
END
$$;

GRANT INSERT, SELECT ON mcp_audit_events TO audit_writer;
GRANT INSERT, SELECT ON skill_audit_events TO audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON mcp_audit_events FROM audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON skill_audit_events FROM audit_writer;

-- Defense in depth: a constraint trigger that raises on any UPDATE or
-- DELETE regardless of the connecting role. Catches accidental admin
-- migrations and rules out the case where the audit_writer grant is
-- bypassed via SECURITY DEFINER functions.
CREATE OR REPLACE FUNCTION audit_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'audit log is append-only; % on % rejected',
    TG_OP, TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS mcp_audit_events_immutable ON mcp_audit_events;
CREATE TRIGGER mcp_audit_events_immutable
  BEFORE UPDATE OR DELETE ON mcp_audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();

DROP TRIGGER IF EXISTS skill_audit_events_immutable ON skill_audit_events;
CREATE TRIGGER skill_audit_events_immutable
  BEFORE UPDATE OR DELETE ON skill_audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();
