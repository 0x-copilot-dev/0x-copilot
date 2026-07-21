-- Rollback for 0033_workspace_defaults_enabled_models.sql.
-- Drops the JSONB column. The enablement resolver falls through to the
-- newest-per-provider heuristic when the column is absent (the record's
-- ``enabled_models`` reads back as None).

ALTER TABLE workspace_defaults
    DROP COLUMN IF EXISTS enabled_models;
