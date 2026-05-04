-- Rollback for 0002_runtime_events_presentation.
ALTER TABLE runtime_events
    DROP COLUMN IF EXISTS activity_kind,
    DROP COLUMN IF EXISTS presentation_json;
