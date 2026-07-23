-- transactional: false
-- 0005_conversation_keyset — conversation keyset-pagination index (PRD-09 D3).
--
-- The Chats archive read (list_conversations, GET /v1/agent/conversations) filters
-- on (org_id, user_id) and, post-PRD-09, orders by (updated_at DESC, id DESC) with
-- a keyset cursor so a pinned/archived row older than page 1 stays reachable. The
-- existing idx_agent_conversations_org_user_active_updated (0001) lacks the `id`
-- tiebreaker, so a keyset can skip or repeat rows when two conversations share an
-- updated_at. This covering-order index adds the tiebreaker for the active
-- (non-deleted) read the surface uses. The pinned bucket keeps its existing
-- partial index (0001: idx_agent_conversations_org_user_pinned_updated).
--
-- CONCURRENTLY so a live deployment can build the index without locking writes to
-- agent_conversations; it therefore cannot run inside a transaction — the
-- `transactional: false` directive above tells the yoyo runner to apply this
-- migration outside a transaction block.
--
-- NOTE (PRD-09 deviation): the PRD pre-assigned this migration id `0004`, but the
-- ai-backend migrations high-water mark advanced to `0004_conversation_project`
-- (PRD-07) before this landed, so `0005` is the free id. Verified on disk.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_conversations_org_user_updated_id
    ON agent_conversations USING btree (org_id, user_id, updated_at DESC, id DESC)
    WHERE deleted_at IS NULL;
