-- C5: Row-Level Security policies and dedicated app/admin roles.
--
-- This migration is intentionally inert: it creates the roles, GRANTs CRUD
-- on tenant-scoped tables to ``enterprise_app``, and registers
-- ``tenant_isolation`` policies that key off
-- ``current_setting('app.current_org_id', true)``. The corresponding
-- ``ENABLE ROW LEVEL SECURITY`` calls live in ``do_rls.sql`` (checked in at
-- the same path but NOT registered with yoyo) so the rollout sequence is:
--
--   1. apply this migration (policies + grants exist but dormant).
--   2. ship adapter code that sets ``app.current_org_id`` on every
--      connection checkout (Stage 2 in docs/roadmap/15-c5-rls-tenant-isolation.md).
--   3. apply do_rls.sql in a separate small PR (Stage 3) once Stage 2 is
--      verified in production.
--
-- Roles are created idempotently inside DO blocks; running this migration
-- against a database where the roles already exist (e.g. SaaS shared cluster)
-- is safe.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        CREATE ROLE enterprise_app NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_admin') THEN
        CREATE ROLE enterprise_admin BYPASSRLS NOINHERIT;
    END IF;
END
$$;

-- Per-table CRUD grants for the app role. Migrations run as enterprise_admin
-- (BYPASSRLS); the app pool authenticates as a user GRANT'd into
-- enterprise_app at deploy time. Done as an explicit list so a new tenant-
-- scoped table added later is not silently exposed to the app role.

GRANT SELECT, INSERT, UPDATE, DELETE ON
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
TO enterprise_app;

-- Sequence usage for tables with serial PKs — none of the listed tables use
-- bigserial today, but extending the grant prevents future migration footguns.
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO enterprise_app;

-- Cross-tenant catalog reads. ``model_pricing`` is global by design and
-- ``runtime_consumer_cursors`` is keyed by (consumer_name, run_id) — neither
-- gets a tenant_isolation policy.
GRANT SELECT ON model_pricing TO enterprise_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_consumer_cursors TO enterprise_app;

-- Tenant-isolation policies. Each uses ``current_setting(..., true)`` so a
-- session that has not set the var simply matches no rows (the missing-setting
-- path returns NULL, and ``org_id = NULL`` is NULL → false in WHERE).
--
-- Policies are dormant until ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY``
-- runs (see do_rls.sql).

CREATE POLICY tenant_isolation ON agent_conversations
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON agent_messages
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON agent_runs
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_events
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- The outbox is read by the worker across all tenants. The OR fallback keeps
-- API-side tenant inserts isolated while letting workers (which set
-- ``app.role='worker'``) drain every tenant's queue.
CREATE POLICY tenant_or_worker ON runtime_outbox_events
    USING (
        current_setting('app.role', true) = 'worker'
        OR org_id = current_setting('app.current_org_id', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        OR org_id = current_setting('app.current_org_id', true)
    );

CREATE POLICY tenant_isolation ON runtime_async_tasks
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_subagent_results
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_tool_invocations
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_approval_requests
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_memory_scopes
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_memory_items
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_context_payloads
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_compression_events
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_capability_snapshots
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_audit_log
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_legal_holds
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_deletion_evidence
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_checkpoints
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_run_usage
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_model_call_usage
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_usage_daily_user
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY tenant_isolation ON runtime_usage_daily_org
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
