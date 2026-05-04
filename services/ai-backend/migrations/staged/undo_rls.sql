-- Stage 5 backout for C5 — disables Row-Level Security on every tenant-
-- scoped table.
--
-- Use only as an incident-response hot patch when a query path missed in
-- Stage 2 wiring is causing legitimate traffic to return zero rows.
-- Re-applying ``do_rls.sql`` after the missing checkout is patched is the
-- recovery step. Apply with the same psql invocation as ``do_rls.sql``.

ALTER TABLE agent_conversations          DISABLE ROW LEVEL SECURITY;
ALTER TABLE agent_messages               DISABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runs                   DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_events               DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_outbox_events        DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_async_tasks          DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_subagent_results     DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_tool_invocations     DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_approval_requests    DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_memory_scopes        DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_memory_items         DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_context_payloads     DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_compression_events   DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_capability_snapshots DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_audit_log            DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_legal_holds          DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_deletion_evidence    DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_checkpoints          DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_run_usage            DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_model_call_usage     DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_daily_user     DISABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_daily_org      DISABLE ROW LEVEL SECURITY;
