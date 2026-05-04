-- B4: Daily rollup tables.
--
-- /v1/usage/me?period=month should return in <50ms even with millions
-- of runtime_run_usage rows. We refresh daily rollups via an idempotent
-- UPSERT loop (NOT a materialized view; explicit tables avoid concurrent
-- refresh foot-guns). The loop recomputes the last 2 days every N
-- minutes — yesterday continues to update for late-arrival window, then
-- is finalized once now() > yesterday_end + USAGE_LATE_ARRIVAL_WINDOW.
--
-- The per-user table excludes rows where pii_purged_at IS NOT NULL
-- (those rows lost their user_id-attributable PII per retention sweep).
-- The per-org table includes them so billing aggregates remain intact.

CREATE TABLE IF NOT EXISTS runtime_usage_daily_user (
    org_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    day                 DATE NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    runs_count          INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, user_id, day, model_provider, model_name)
);
CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_user_org_day
    ON runtime_usage_daily_user (org_id, day DESC);

CREATE TABLE IF NOT EXISTS runtime_usage_daily_org (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    runs_count          INTEGER NOT NULL,
    distinct_users      INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, model_provider, model_name)
);
CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_org_day
    ON runtime_usage_daily_org (org_id, day DESC);
