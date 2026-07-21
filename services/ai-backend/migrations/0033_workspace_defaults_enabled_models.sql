-- PR-2C: per-workspace model curation.
--
-- Adds one nullable JSONB column to ``workspace_defaults`` (PR 1.6 /
-- migration 0019) holding the list of model ids/model_names the
-- workspace has explicitly enabled for its pickers:
--
--   ["gpt-5.4-mini", "anthropic/claude-sonnet-4-6", ...]
--
-- Storage shape rationale:
--   * A JSON array in one column — it is a flat, order-preserving list
--     the admin edits as a whole (full-document replace), never queried
--     by element. A join table would be six times the machinery for a
--     curation list that ships as one blob with the rest of the row.
--   * NULLABLE with NO default — SQL NULL is meaningful here: it means
--     "no explicit curation", which the enablement resolver reads as
--     "enable the newest models per configured provider". An empty
--     array ``[]`` is distinct: "the workspace disabled everything".
--     A ``NOT NULL DEFAULT`` would collapse that distinction.
--   * Metadata-only add on Postgres 11+ (nullable, no default) — no row
--     rewrite; existing rows read back as NULL → heuristic default.
--
-- RLS unchanged: the table-level ``tenant_isolation`` policy from
-- migration 0019 covers this column too.

ALTER TABLE workspace_defaults
    ADD COLUMN IF NOT EXISTS enabled_models JSONB;
