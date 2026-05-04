DROP POLICY IF EXISTS tenant_or_global ON runtime_tool_budgets;
REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime_tool_budgets FROM enterprise_app;
DROP INDEX IF EXISTS idx_runtime_tool_budgets_org;
DROP TABLE IF EXISTS runtime_tool_budgets;
