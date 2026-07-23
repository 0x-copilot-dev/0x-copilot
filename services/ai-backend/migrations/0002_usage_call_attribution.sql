-- 0002_usage_call_attribution — Generative Surfaces v2 per-call usage
-- attribution (PRD-A2, FR-G; ../../docs/plan/generative-surfaces-v2/02-sdr.md §8).
--
-- Adds nullable per-LLM-call user + surface attribution columns to
-- runtime_model_call_usage so usage is queryable per-user (E3 rollups) and per
-- shaped surface (B4 shape-requests). Pure additive ALTER: nullable columns +
-- one covering index, safe to apply as a separate deploy step
-- (RUNTIME_MIGRATIONS_AUTO_APPLY=false). Pre-migration rows keep NULL; the
-- record's schema_version stays 1.

ALTER TABLE runtime_model_call_usage ADD COLUMN user_id text;
ALTER TABLE runtime_model_call_usage ADD COLUMN surface_id text;

-- Per-user usage rollups scan (org_id, user_id, created_at); partial on
-- user_id so pre-migration NULL rows are excluded from the index.
CREATE INDEX idx_runtime_model_call_usage_org_user_created
    ON runtime_model_call_usage (org_id, user_id, created_at)
    WHERE user_id IS NOT NULL;
