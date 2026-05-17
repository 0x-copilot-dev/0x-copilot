-- B7: per-org and per-user spend budgets with atomic CAS charging.
--
-- Three tables:
--   usage_budgets             — config (limits, scope, period, enforcement).
--   usage_budget_state        — current period's running spend per budget.
--                               Updated via CAS on row_version + idempotency
--                               on last_charged_run_id.
--   usage_budget_reservations — pre-flight reservations so two concurrent
--                               runs cannot both pass a check that allows
--                               only one. Reaper purges expired entries.
--
-- All three tables are tenant-scoped (org_id leads each row); RLS policies
-- and the enterprise_app grant are added at the bottom so a future RLS
-- enable doesn't silently miss them.

CREATE TABLE IF NOT EXISTS usage_budgets (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    user_id             TEXT,                              -- NULL = org-scope
    scope               TEXT NOT NULL CHECK (scope IN ('org','user')),
    period              TEXT NOT NULL CHECK (period IN ('day','month')),
    enforcement         TEXT NOT NULL CHECK (enforcement IN ('soft','hard')),
    limit_micro_usd     BIGINT,
    limit_tokens        BIGINT,
    status              TEXT NOT NULL CHECK (status IN ('active','disabled')),
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    created_by_user_id  TEXT NOT NULL
);

-- COALESCE → '<org>' so the unique index treats org-scope rows as a
-- single user_id slot per (org, scope, period). The UNIQUE table-constraint
-- form (UNIQUE (cols)) only accepts plain column names, so this expression
-- has to live in a CREATE UNIQUE INDEX statement.
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_budgets_scope
    ON usage_budgets (org_id, COALESCE(user_id, '<org>'), scope, period);

CREATE INDEX IF NOT EXISTS idx_usage_budgets_org_status
    ON usage_budgets (org_id, status);

CREATE TABLE IF NOT EXISTS usage_budget_state (
    budget_id                  TEXT NOT NULL REFERENCES usage_budgets(id) ON DELETE CASCADE,
    period_start               DATE NOT NULL,
    period_end                 DATE NOT NULL,
    current_spend_micro_usd    BIGINT NOT NULL DEFAULT 0,
    current_spend_tokens       BIGINT NOT NULL DEFAULT 0,
    row_version                INTEGER NOT NULL DEFAULT 1,
    last_charged_run_id        TEXT,
    updated_at                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (budget_id, period_start)
);

CREATE TABLE IF NOT EXISTS usage_budget_reservations (
    reservation_id     TEXT PRIMARY KEY,
    budget_id          TEXT NOT NULL REFERENCES usage_budgets(id) ON DELETE CASCADE,
    period_start       DATE NOT NULL,
    run_id             TEXT NOT NULL,
    reserved_micro_usd BIGINT NOT NULL DEFAULT 0,
    reserved_tokens    BIGINT NOT NULL DEFAULT 0,
    expires_at         TIMESTAMPTZ NOT NULL,
    consumed_at        TIMESTAMPTZ
);

-- Active reservations contribute to the "remaining" headroom calculation;
-- the partial index keeps the lookup fast even after years of consumed rows.
CREATE INDEX IF NOT EXISTS idx_usage_budget_reservations_active
    ON usage_budget_reservations (budget_id, period_start)
    WHERE consumed_at IS NULL;

-- Reaper scan: bounded by the partial index — it only walks unconsumed rows.
CREATE INDEX IF NOT EXISTS idx_usage_budget_reservations_expiring
    ON usage_budget_reservations (expires_at)
    WHERE consumed_at IS NULL;

-- Same-(budget_id, run_id) reservations are an idempotency guard: a
-- worker retry must reuse the existing reservation, never duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_budget_reservations_run
    ON usage_budget_reservations (budget_id, run_id)
    WHERE consumed_at IS NULL;

-- Grant CRUD to enterprise_app so RLS can take effect without breaking writes.
-- usage_budget_state has no org_id column (the scope flows through usage_budgets);
-- it's gated via the FK. Reservations carry budget_id; same story.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    usage_budgets,
    usage_budget_state,
    usage_budget_reservations
TO enterprise_app;

-- Tenant isolation on usage_budgets. State and reservations inherit
-- isolation through the FK to usage_budgets — Postgres FK joins respect
-- RLS on the parent. We add explicit policies below for defense-in-depth
-- so a manual SELECT against usage_budget_state without going through
-- usage_budgets still gets blocked.
CREATE POLICY tenant_isolation ON usage_budgets
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- Worker-OR-tenant policy: worker process needs to write charges across
-- tenants in a single connection. Same pattern as runtime_outbox_events.
CREATE POLICY tenant_or_worker ON usage_budget_state
    USING (
        current_setting('app.role', true) = 'worker'
        OR EXISTS (
            SELECT 1 FROM usage_budgets b
             WHERE b.id = usage_budget_state.budget_id
               AND b.org_id = current_setting('app.current_org_id', true)
        )
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        OR EXISTS (
            SELECT 1 FROM usage_budgets b
             WHERE b.id = usage_budget_state.budget_id
               AND b.org_id = current_setting('app.current_org_id', true)
        )
    );

CREATE POLICY tenant_or_worker ON usage_budget_reservations
    USING (
        current_setting('app.role', true) = 'worker'
        OR EXISTS (
            SELECT 1 FROM usage_budgets b
             WHERE b.id = usage_budget_reservations.budget_id
               AND b.org_id = current_setting('app.current_org_id', true)
        )
    )
    WITH CHECK (
        current_setting('app.role', true) = 'worker'
        OR EXISTS (
            SELECT 1 FROM usage_budgets b
             WHERE b.id = usage_budget_reservations.budget_id
               AND b.org_id = current_setting('app.current_org_id', true)
        )
    );
