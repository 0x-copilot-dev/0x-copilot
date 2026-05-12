-- C8 Phase 2: per-row `retention_until` column on the three tables that
-- today use ``created_at + ttl < NOW()`` in the sweep SQL.
--
-- The column is nullable. NULL means "no retention policy applies, or the
-- row pre-dates the backfill job." Phase 3 will stamp the value at INSERT
-- time. The opt-in RetentionBackfillJob (RETENTION_BACKFILL_ENABLED=true)
-- fills existing rows in chunks so the migration itself is non-blocking.
--
-- Index note: CREATE INDEX without CONCURRENTLY runs inside the yoyo
-- transaction. For empty or small tables in dev this is fine. For tables
-- with significant row counts operators can create the index manually with
-- CONCURRENTLY before running this migration; yoyo will then skip it via
-- the IF NOT EXISTS guard.

ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ;

ALTER TABLE runtime_events
    ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ;

ALTER TABLE runtime_memory_items
    ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ;

-- Partial indexes: only rows with a retention deadline are covered.
-- Phase 4 changes the sweep WHERE to ``retention_until < NOW()`` so
-- scans stay O(rows-due) rather than O(all-rows).

CREATE INDEX IF NOT EXISTS idx_agent_messages_retention_until
    ON agent_messages (org_id, retention_until)
    WHERE retention_until IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_runtime_events_retention_until
    ON runtime_events (org_id, retention_until)
    WHERE retention_until IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_runtime_memory_items_retention_until
    ON runtime_memory_items (org_id, retention_until)
    WHERE retention_until IS NOT NULL;
