-- Add presentation columns to runtime_events.
--
-- Historically applied as an ad-hoc ALTER inside the legacy migrate() block in
-- runtime_api_store.py; promoted here so it is versioned, reversible, and
-- visible in the migration history.

ALTER TABLE runtime_events
    ADD COLUMN IF NOT EXISTS activity_kind TEXT,
    ADD COLUMN IF NOT EXISTS presentation_json JSONB;
