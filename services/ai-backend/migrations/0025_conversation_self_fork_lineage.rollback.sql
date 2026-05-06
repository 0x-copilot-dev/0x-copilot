DROP INDEX IF EXISTS idx_agent_conversations_forked_from_message;

ALTER TABLE agent_conversations
    DROP COLUMN IF EXISTS forked_from_message_id;
