-- B8: per-tool call-count + input-token budgets, code-enforced.
--
-- One row per (org_id, tool_name). org_id NULL = global default; the
-- seed row is the existing RUNTIME_TOOL_CALL_BUDGET so behavior is
-- byte-for-byte preserved on day one. Per-tool overrides (e.g. a
-- tighter cap on web_search) live as additional rows.
--
-- Resolution at lookup: most-specific match wins.
--   exact (org_id, tool_name)  >  (org_id, '*')
--                              >  (NULL, tool_name)
--                              >  (NULL, '*')
-- The seed default is (NULL, '*'); custom rules supersede it.

CREATE TABLE IF NOT EXISTS runtime_tool_budgets (
    id                          TEXT PRIMARY KEY,
    org_id                      TEXT,
    tool_name                   TEXT NOT NULL,
    max_calls_per_run           INTEGER NOT NULL CHECK (max_calls_per_run >= 1),
    max_input_tokens_per_call   INTEGER,
    max_input_tokens_per_run    INTEGER,
    enforcement                 TEXT NOT NULL CHECK (enforcement IN ('soft','hard')),
    created_at                  TIMESTAMPTZ NOT NULL,
    updated_at                  TIMESTAMPTZ NOT NULL
);

-- The global-row sentinel '<global>' collapses NULL org_ids into a single
-- distinct slot per tool_name. UNIQUE table-constraint syntax doesn't
-- accept expressions, so the collapse has to live in a UNIQUE INDEX.
CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_tool_budgets_scope
    ON runtime_tool_budgets (COALESCE(org_id, '<global>'), tool_name);

CREATE INDEX IF NOT EXISTS idx_runtime_tool_budgets_org
    ON runtime_tool_budgets (org_id);

-- Seed the global default with the same value as RUNTIME_TOOL_CALL_BUDGET
-- so the rollout is no-op for orgs that haven't configured anything.
INSERT INTO runtime_tool_budgets (
    id, org_id, tool_name, max_calls_per_run, enforcement, created_at, updated_at
) VALUES (
    'seed_default', NULL, '*', 6, 'hard', now(), now()
) ON CONFLICT DO NOTHING;

-- Grant + RLS in line with B7's pattern.
GRANT SELECT, INSERT, UPDATE, DELETE ON runtime_tool_budgets TO enterprise_app;

-- The global row (org_id IS NULL) must remain readable by every tenant
-- so middleware can fall back to it. Per-org rows enforce isolation
-- normally. ``app.role='worker'`` reads everything for cross-tenant
-- middleware lookups (matches the runtime_outbox_events pattern).
CREATE POLICY tenant_or_global ON runtime_tool_budgets
    USING (
        org_id IS NULL
        OR current_setting('app.role', true) = 'worker'
        OR org_id = current_setting('app.current_org_id', true)
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        OR org_id = current_setting('app.current_org_id', true)
    );
