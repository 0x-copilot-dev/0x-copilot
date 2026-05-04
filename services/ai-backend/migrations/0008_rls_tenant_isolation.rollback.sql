-- Rollback for 0008_rls_tenant_isolation.sql.
--
-- Drops policies first (safe whether or not the corresponding ENABLE ROW
-- LEVEL SECURITY has been applied via do_rls.sql), then revokes grants. The
-- ``enterprise_app`` and ``enterprise_admin`` roles are intentionally NOT
-- dropped — they may have been pre-provisioned by ops outside this migration
-- and other databases on the same cluster may still depend on them.

DROP POLICY IF EXISTS tenant_isolation ON agent_conversations;
DROP POLICY IF EXISTS tenant_isolation ON agent_messages;
DROP POLICY IF EXISTS tenant_isolation ON agent_runs;
DROP POLICY IF EXISTS tenant_isolation ON runtime_events;
DROP POLICY IF EXISTS tenant_or_worker ON runtime_outbox_events;
DROP POLICY IF EXISTS tenant_isolation ON runtime_async_tasks;
DROP POLICY IF EXISTS tenant_isolation ON runtime_subagent_results;
DROP POLICY IF EXISTS tenant_isolation ON runtime_tool_invocations;
DROP POLICY IF EXISTS tenant_isolation ON runtime_approval_requests;
DROP POLICY IF EXISTS tenant_isolation ON runtime_memory_scopes;
DROP POLICY IF EXISTS tenant_isolation ON runtime_memory_items;
DROP POLICY IF EXISTS tenant_isolation ON runtime_context_payloads;
DROP POLICY IF EXISTS tenant_isolation ON runtime_compression_events;
DROP POLICY IF EXISTS tenant_isolation ON runtime_capability_snapshots;
DROP POLICY IF EXISTS tenant_isolation ON runtime_audit_log;
DROP POLICY IF EXISTS tenant_isolation ON runtime_legal_holds;
DROP POLICY IF EXISTS tenant_isolation ON runtime_deletion_evidence;
DROP POLICY IF EXISTS tenant_isolation ON runtime_checkpoints;
DROP POLICY IF EXISTS tenant_isolation ON runtime_run_usage;
DROP POLICY IF EXISTS tenant_isolation ON runtime_model_call_usage;
DROP POLICY IF EXISTS tenant_isolation ON runtime_usage_daily_user;
DROP POLICY IF EXISTS tenant_isolation ON runtime_usage_daily_org;

REVOKE SELECT, INSERT, UPDATE, DELETE ON
    agent_conversations,
    agent_messages,
    agent_runs,
    runtime_events,
    runtime_outbox_events,
    runtime_async_tasks,
    runtime_subagent_results,
    runtime_tool_invocations,
    runtime_approval_requests,
    runtime_memory_scopes,
    runtime_memory_items,
    runtime_context_payloads,
    runtime_compression_events,
    runtime_capability_snapshots,
    runtime_audit_log,
    runtime_legal_holds,
    runtime_deletion_evidence,
    runtime_checkpoints,
    runtime_run_usage,
    runtime_model_call_usage,
    runtime_usage_daily_user,
    runtime_usage_daily_org
FROM enterprise_app;

REVOKE SELECT ON model_pricing FROM enterprise_app;
REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime_consumer_cursors FROM enterprise_app;
