-- Rollback PRD-H.4 conversation pin flag.
DROP INDEX IF EXISTS idx_agent_conversations_org_user_pinned_updated;

ALTER TABLE agent_conversations
    DROP COLUMN IF EXISTS pinned;
