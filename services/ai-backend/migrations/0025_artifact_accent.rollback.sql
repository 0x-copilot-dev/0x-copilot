ALTER TABLE runtime_artifacts
    DROP CONSTRAINT IF EXISTS runtime_artifacts_accent_check,
    DROP COLUMN IF EXISTS accent;
