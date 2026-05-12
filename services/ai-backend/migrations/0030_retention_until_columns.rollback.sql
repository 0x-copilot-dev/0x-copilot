DROP INDEX IF EXISTS idx_agent_messages_retention_until;
DROP INDEX IF EXISTS idx_runtime_events_retention_until;
DROP INDEX IF EXISTS idx_runtime_memory_items_retention_until;

ALTER TABLE agent_messages      DROP COLUMN IF EXISTS retention_until;
ALTER TABLE runtime_events      DROP COLUMN IF EXISTS retention_until;
ALTER TABLE runtime_memory_items DROP COLUMN IF EXISTS retention_until;
