-- B3: Versioned pricing catalog + cost columns on usage rows.
--
-- Cost is stored in micro-USD integer (1 USD = 1_000_000 micro_usd) so
-- no float drift can creep in on the persistence path. Every usage row
-- snapshots the pricing_id + pricing_version it was costed against, so
-- a re-pricing of today's model never mutates yesterday's cost.
--
-- The partial unique index on (provider, model_name, region) WHERE
-- effective_until IS NULL enforces "exactly one active row per triple"
-- so lookup-at-time queries are unambiguous.

CREATE TABLE IF NOT EXISTS model_pricing (
    id                                 TEXT PRIMARY KEY,
    provider                           TEXT NOT NULL,
    model_name                         TEXT NOT NULL,
    region                             TEXT NOT NULL DEFAULT 'global',
    effective_from                     TIMESTAMPTZ NOT NULL,
    effective_until                    TIMESTAMPTZ,
    input_per_1m_micro_usd             BIGINT NOT NULL CHECK (input_per_1m_micro_usd >= 0),
    output_per_1m_micro_usd            BIGINT NOT NULL CHECK (output_per_1m_micro_usd >= 0),
    cached_input_per_1m_micro_usd      BIGINT,
    context_window_tokens              INTEGER,
    pricing_source                     TEXT NOT NULL CHECK (pricing_source IN ('yaml-seed','admin-override','partner-feed')),
    pricing_version                    TEXT NOT NULL,
    created_at                         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup
    ON model_pricing (provider, model_name, region, effective_from DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_pricing_active
    ON model_pricing (provider, model_name, region) WHERE effective_until IS NULL;

-- Cost columns added nullable to runtime_run_usage and
-- runtime_model_call_usage so unseeded deployments stay null-safe and
-- the worker hook in B3 can compute cost in a follow-up write.
-- ``IF NOT EXISTS`` lets the migration be re-applied without errors.
ALTER TABLE runtime_run_usage
    ADD COLUMN IF NOT EXISTS cost_micro_usd BIGINT,
    ADD COLUMN IF NOT EXISTS pricing_id TEXT,
    ADD COLUMN IF NOT EXISTS pricing_version TEXT;

ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS cost_micro_usd BIGINT,
    ADD COLUMN IF NOT EXISTS pricing_id TEXT,
    ADD COLUMN IF NOT EXISTS pricing_version TEXT;
