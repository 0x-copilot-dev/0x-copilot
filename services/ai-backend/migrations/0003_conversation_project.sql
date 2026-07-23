-- transactional: false
-- 0003_conversation_project — file a conversation under a project (PRD-07 Seam 1).
--
-- A project's chat list IS the conversation list filtered by project. That
-- filter axis + the GROUP BY that backs `GET /v1/agent/conversations/counts`
-- must be a real column with an index — not a `metadata` JSONB key (the same
-- reasoning that forced `pinned` out of metadata into a column in 0034).
--
-- `project_id` is a LOOSE reference: projects live in a different service's
-- database, so there is no FK (the same rationale `projects.owner_user_id`
-- documents at 0043_projects.sql:31). Nullable, with NO backfill — pre-existing
-- conversations have no project and inventing one would be a lie.
--
-- The partial index drives the project-scoped list + the per-project count on
-- the (org_id, project_id, updated_at DESC) keyset. CONCURRENTLY so a live
-- deployment builds it without locking writes to agent_conversations; it
-- therefore cannot run inside a transaction, hence `transactional: false`.

ALTER TABLE agent_conversations ADD COLUMN IF NOT EXISTS project_id text;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_conversations_project
    ON agent_conversations (org_id, project_id, updated_at DESC)
    WHERE project_id IS NOT NULL AND deleted_at IS NULL;
