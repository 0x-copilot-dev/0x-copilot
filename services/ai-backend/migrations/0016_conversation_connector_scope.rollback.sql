-- Rollback PR 1.2: drop per-chat connector scope persistence.
DROP INDEX IF EXISTS idx_agent_conversations_enabled_connectors;

ALTER TABLE agent_conversations
    DROP COLUMN IF EXISTS connectors_updated_at,
    DROP COLUMN IF EXISTS enabled_connectors;
