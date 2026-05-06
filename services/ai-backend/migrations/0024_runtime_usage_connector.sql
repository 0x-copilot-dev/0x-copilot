-- PR 7.2: per-connector token attribution.
--
-- Adds connector attribution to per-LLM-call usage rows + a daily rollup
-- table the workspace "By connector" view reads from.
--
-- Storage shape:
--   runtime_model_call_usage.connector_slug TEXT NULL
--     ─ NULL for calls before any tool fires this turn (cold-turn / planning).
--     ─ Otherwise the connector_slug of the most recent completed
--       runtime_tool_invocations row on the same run with
--       completed_at < the call's created_at. Failed invocations are
--       skipped. Rule lives in run_metrics.py (worker emit site).
--
--   runtime_usage_daily_connector
--     ─ Mirrors runtime_usage_daily_user / runtime_usage_daily_org from
--       migration 0007. PK includes connector_slug; the rollup loop
--       coalesces NULL connector to '' so the (org_id, day, '') row
--       represents "(unattributed)" calls.
--
-- Why a rollup table over a materialised view: same rationale as 0007 —
-- explicit UPSERT keeps idempotency tight, no REFRESH MATERIALIZED VIEW
-- CONCURRENTLY foot-gun. See docs/new-design/pr-7.2-...md §3.4.

ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS connector_slug TEXT;

-- Hot-path index: per-connector aggregation over a 30-day window.
-- Partial index keeps unattributed rows out (we never aggregate the
-- (unattributed) bucket via this index — it's computed from the rollup
-- table's '' row instead).
CREATE INDEX IF NOT EXISTS idx_runtime_model_call_usage_org_connector_created
    ON runtime_model_call_usage (org_id, connector_slug, created_at)
    WHERE connector_slug IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime_usage_daily_connector (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    connector_slug      TEXT NOT NULL,
    runs_count          INTEGER NOT NULL,
    distinct_users      INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, connector_slug)
);
CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_connector_org_day
    ON runtime_usage_daily_connector (org_id, day DESC);

-- RLS: same pattern as migration 0008 (already enabled on the sibling
-- daily-usage tables). Cross-tenant reads physically impossible.
ALTER TABLE runtime_usage_daily_connector ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_usage_daily_connector
    USING (org_id = current_setting('app.current_org', true));
