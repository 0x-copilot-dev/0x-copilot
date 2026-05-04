-- Phase 3 audit hardening for the runtime_audit_log.
--
-- Each row stores HMAC chain fields signed by AuditChainSigner. Chain is
-- per-(table, org_id). Rows written before this migration have NULL
-- signature and are flagged invalid by the verifier — the intentional
-- signal that integrity proof started at a known migration boundary.
ALTER TABLE runtime_audit_log
    ADD COLUMN IF NOT EXISTS seq BIGINT,
    ADD COLUMN IF NOT EXISTS prev_hash BYTEA,
    ADD COLUMN IF NOT EXISTS signature BYTEA,
    ADD COLUMN IF NOT EXISTS key_version SMALLINT;

CREATE INDEX IF NOT EXISTS idx_runtime_audit_log_org_seq
    ON runtime_audit_log (org_id, seq);

-- Append-only role: INSERT/SELECT only on runtime_audit_log. The
-- ai-backend's audit-emitting code paths must connect as audit_writer in
-- production so a compromised app process cannot mutate history.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
        CREATE ROLE audit_writer NOLOGIN;
    END IF;
END
$$;

GRANT INSERT, SELECT ON runtime_audit_log TO audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON runtime_audit_log FROM audit_writer;

-- Defense in depth: a constraint trigger that raises on any UPDATE or
-- DELETE regardless of the connecting role. Catches accidental admin
-- migrations and SECURITY DEFINER bypasses.
CREATE OR REPLACE FUNCTION runtime_audit_log_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $func$
BEGIN
    RAISE EXCEPTION 'audit log is append-only; % on % rejected',
        TG_OP, TG_TABLE_NAME;
END;
$func$;

DROP TRIGGER IF EXISTS runtime_audit_log_immutable ON runtime_audit_log;
CREATE TRIGGER runtime_audit_log_immutable
    BEFORE UPDATE OR DELETE ON runtime_audit_log
    FOR EACH ROW EXECUTE FUNCTION runtime_audit_log_immutable_guard();
