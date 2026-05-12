-- Sub-PRD 01d: rollup expansion (subagent + purpose tables, connector PK extension).
--
-- Three changes:
--
-- 1. ``runtime_usage_daily_connector`` gains ``model_name`` and its PK
--    extends to include it. Pre-migration rows take ``model_name=''``
--    so cross-model splits land in distinct rows going forward while
--    historical aggregates stay readable as the "(no model)" bucket.
--
-- 2. New table ``runtime_usage_daily_subagent`` — org-scoped daily
--    rollup keyed on ``(org_id, day, subagent_slug, provider, model)``.
--    Carries every token kind 01a captured so per-subagent reports are
--    total-correct.
--
-- 3. New table ``runtime_usage_daily_purpose`` — same shape as
--    subagent but keyed on ``purpose`` (Purpose StrEnum).
--
-- Migration safety: ADD COLUMN NOT NULL DEFAULT '' is metadata-only on
-- PG ≥11; DROP/ADD PRIMARY KEY rebuilds the btree under an
-- AccessExclusive lock but does not rewrite the table (existing rows
-- already satisfy uniqueness on the broader key with model_name='').

-- 1. Connector rollup PK extension --------------------------------------

ALTER TABLE runtime_usage_daily_connector
    ADD COLUMN IF NOT EXISTS model_name TEXT NOT NULL DEFAULT '';

ALTER TABLE runtime_usage_daily_connector
    DROP CONSTRAINT IF EXISTS runtime_usage_daily_connector_pkey;

ALTER TABLE runtime_usage_daily_connector
    ADD CONSTRAINT runtime_usage_daily_connector_pkey
        PRIMARY KEY (org_id, day, connector_slug, model_name);

-- 2. Subagent rollup table ---------------------------------------------

CREATE TABLE IF NOT EXISTS runtime_usage_daily_subagent (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    subagent_slug       TEXT NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    call_count          INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens    BIGINT NOT NULL DEFAULT 0,
    audio_input_tokens  BIGINT NOT NULL DEFAULT 0,
    audio_output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, subagent_slug, model_provider, model_name)
);

CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_subagent_org_day
    ON runtime_usage_daily_subagent (org_id, day DESC);

ALTER TABLE runtime_usage_daily_subagent ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_usage_daily_subagent
    USING (org_id = current_setting('app.current_org', true));

-- 3. Purpose rollup table ----------------------------------------------

CREATE TABLE IF NOT EXISTS runtime_usage_daily_purpose (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    purpose             TEXT NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    call_count          INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens    BIGINT NOT NULL DEFAULT 0,
    audio_input_tokens  BIGINT NOT NULL DEFAULT 0,
    audio_output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, purpose, model_provider, model_name)
);

CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_purpose_org_day
    ON runtime_usage_daily_purpose (org_id, day DESC);

ALTER TABLE runtime_usage_daily_purpose ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_usage_daily_purpose
    USING (org_id = current_setting('app.current_org', true));
