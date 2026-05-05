-- Rollback for 0021_workspace_defaults_behavior_overrides.sql.
-- Drops the JSONB column. The runtime resolver falls through to
-- deployment defaults when the column is absent (the optional getter
-- on ``WorkspaceDefaultsRecord`` already handles None).

ALTER TABLE workspace_defaults
    DROP COLUMN IF EXISTS behavior_overrides;
