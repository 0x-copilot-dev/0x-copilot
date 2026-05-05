-- PR 1.6: conversation lifecycle (soft-delete + folder + fork lineage).
--
-- ``deleted_at``              TIMESTAMPTZ — soft-delete tombstone. The
--                              C8 retention sweeper reaps the row once
--                              the org's effective ``messages`` TTL
--                              elapses; until then ``POST .../restore``
--                              clears the column.
--
-- ``folder``                  TEXT — flat string label the sidebar
--                              groups by. No folder table, no folder
--                              ACL — folders are personal organisational
--                              labels in v1 per docs/new-design/pr-1.6...md §2.3.3.
--
-- ``parent_conversation_id``  TEXT — forward-declared for Wave 6 fork
--                              lineage. Nullable + unindexed today;
--                              Wave 6 adds the FK self-reference and
--                              an ``(parent_conversation_id)`` index in
--                              its own migration so this PR ships a
--                              schema-only forward declaration.
--
-- We do NOT widen the ``status`` enum (active/archived). The
-- ``deleted_at`` timestamp captures both the state and when the action
-- happened, which composes better than a third enum value.
--
-- Index strategy:
--   1. The hot sidebar query is "active, undeleted, this user's
--      conversations newest-first". A new partial index on
--      (org_id, user_id, updated_at DESC) WHERE deleted_at IS NULL is
--      what ``list_conversations`` will use after this PR.
--   2. Folder filter is sparse (most rows have NULL folder); a partial
--      index covers the per-folder query without bloating storage.
--   3. The existing ``idx_agent_conversations_org_user_updated`` index
--      stays untouched — other code paths may rely on its full coverage
--      and dropping it would force a rewrite.
--
-- Note on index creation: this migration uses CREATE INDEX (in a
-- transaction via yoyo) rather than CREATE INDEX CONCURRENTLY. For
-- tables with significant existing rows in production, operators
-- should pre-create the indexes concurrently before applying; the
-- IF NOT EXISTS makes the migration a no-op in that case.

ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS deleted_at              TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS folder                  TEXT,
    ADD COLUMN IF NOT EXISTS parent_conversation_id  TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_conversations_org_user_active_updated
    ON agent_conversations (org_id, user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_agent_conversations_folder
    ON agent_conversations (org_id, user_id, folder, updated_at DESC)
    WHERE folder IS NOT NULL AND deleted_at IS NULL;
