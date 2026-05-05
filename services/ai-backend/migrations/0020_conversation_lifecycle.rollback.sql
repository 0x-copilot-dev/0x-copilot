-- Rollback PR 1.6 conversation lifecycle.
DROP INDEX IF EXISTS idx_agent_conversations_folder;
DROP INDEX IF EXISTS idx_agent_conversations_org_user_active_updated;

ALTER TABLE agent_conversations
    DROP COLUMN IF EXISTS parent_conversation_id,
    DROP COLUMN IF EXISTS folder,
    DROP COLUMN IF EXISTS deleted_at;
