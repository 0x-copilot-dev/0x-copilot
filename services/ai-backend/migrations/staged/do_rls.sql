-- Stage 3 of C5 — turns on Row-Level Security for every tenant-scoped table.
--
-- This file is intentionally NOT a yoyo-managed migration: it is applied
-- once Stage 2 (every adapter checkout sets ``app.current_org_id``) has
-- been verified in production and rolled out to every replica. Apply by
-- piping into psql with the migration role:
--
--   PGAPPNAME=ai-backend:rls-stage3 \
--   psql "$DATABASE_URL" \
--     -v ON_ERROR_STOP=1 \
--     -f services/ai-backend/migrations/do_rls.sql
--
-- The companion ``undo_rls.sql`` reverses every ENABLE statement should a
-- production incident require fast disablement (Stage 5 backout).

ALTER TABLE agent_conversations          ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_messages               ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runs                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_events               ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_outbox_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_async_tasks          ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_subagent_results     ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_tool_invocations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_approval_requests    ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_memory_scopes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_memory_items         ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_context_payloads     ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_compression_events   ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_capability_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_audit_log            ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_legal_holds          ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_deletion_evidence    ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_checkpoints          ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_run_usage            ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_model_call_usage     ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_daily_user     ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_daily_org      ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_citations            ENABLE ROW LEVEL SECURITY;

-- ``FORCE`` makes RLS apply to the table owner as well, closing the
-- escape hatch where a misconfigured deploy connects as the table owner
-- and bypasses tenant isolation.
ALTER TABLE agent_conversations          FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_messages               FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runs                   FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_events               FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_outbox_events        FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_async_tasks          FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_subagent_results     FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_tool_invocations     FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_approval_requests    FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_memory_scopes        FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_memory_items         FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_context_payloads     FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_compression_events   FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_capability_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_audit_log            FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_legal_holds          FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_deletion_evidence    FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_checkpoints          FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_run_usage            FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_model_call_usage     FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_daily_user     FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_daily_org      FORCE ROW LEVEL SECURITY;
ALTER TABLE runtime_citations            FORCE ROW LEVEL SECURITY;
