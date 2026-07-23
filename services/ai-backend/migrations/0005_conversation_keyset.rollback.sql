-- transactional: false
-- Rollback 0005_conversation_keyset: drop the conversation keyset index.
-- CONCURRENTLY (outside a transaction, per the `transactional: false` directive)
-- so the drop does not take a lock that blocks concurrent readers/writers.
DROP INDEX CONCURRENTLY IF EXISTS idx_agent_conversations_org_user_updated_id;
