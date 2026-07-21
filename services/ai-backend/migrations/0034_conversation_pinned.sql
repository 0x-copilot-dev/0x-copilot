-- PRD-H.4: first-class conversation pin flag.
--
-- ``pinned``  BOOLEAN NOT NULL DEFAULT false — drives the Chats
--             "Pinned" section. Previously the frontend read
--             ``metadata.pinned`` which no backend path ever wrote, so
--             the Pinned bucket was always empty. This promotes pin to a
--             real column toggled by ``POST /v1/agent/conversations/{id}/pin``.
--
-- Additive + backfilled to false, so existing rows and older clients are
-- unaffected. The Chats-list ``preview`` / ``model`` fields are pure
-- read-time projections (last message snippet / latest run model) and
-- need no columns.
--
-- Index strategy: the sidebar sorts pinned-first then newest-first for a
-- given user. A partial index on the pinned rows keeps that query cheap
-- without bloating storage (most rows are unpinned → NULL-of-the-partial).

ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_agent_conversations_org_user_pinned_updated
    ON agent_conversations (org_id, user_id, updated_at DESC)
    WHERE pinned AND deleted_at IS NULL;
