DROP POLICY IF EXISTS tenant_or_worker ON usage_budget_reservations;
DROP POLICY IF EXISTS tenant_or_worker ON usage_budget_state;
DROP POLICY IF EXISTS tenant_isolation ON usage_budgets;

REVOKE SELECT, INSERT, UPDATE, DELETE ON
    usage_budget_reservations,
    usage_budget_state,
    usage_budgets
FROM enterprise_app;

DROP INDEX IF EXISTS uq_usage_budget_reservations_run;
DROP INDEX IF EXISTS idx_usage_budget_reservations_expiring;
DROP INDEX IF EXISTS idx_usage_budget_reservations_active;
DROP TABLE IF EXISTS usage_budget_reservations;

DROP TABLE IF EXISTS usage_budget_state;

DROP INDEX IF EXISTS idx_usage_budgets_org_status;
DROP TABLE IF EXISTS usage_budgets;
