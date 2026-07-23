-- transactional: false
-- Rollback 0003_conversation_project: drop the project index + column.
-- CONCURRENTLY (outside a transaction, per the `transactional: false` directive)
-- so the drop does not take a lock that blocks concurrent readers/writers.
-- Dropping the column is safe: it is nullable and nothing else reads it, so an
-- existing conversation is unaffected by losing its (optional) project link.

DROP INDEX CONCURRENTLY IF EXISTS idx_agent_conversations_project;

ALTER TABLE agent_conversations DROP COLUMN IF EXISTS project_id;
