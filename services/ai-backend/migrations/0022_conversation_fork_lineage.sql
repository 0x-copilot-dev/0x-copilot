-- PR 6.2: conversation fork lineage.
--
-- Completes the lineage forward-declared by PR 1.6 (migration 0020):
--   * Adds the FK self-reference on agent_conversations.parent_conversation_id
--     with ON DELETE SET NULL — never cascade. A user deleting the *source*
--     chat must not destroy a recipient's fork; the pointer becomes NULL but
--     the audit pointer ``forked_from_share_id`` survives so the audit chain
--     still threads back to the share that authorised the fork.
--   * Adds the new column ``forked_from_share_id`` (no FK — share rows can
--     be revoked / cleaned up independently of the conversation row that
--     they authorised).
--   * Adds a sparse index on ``parent_conversation_id`` so queries like
--     "all forks of conversation X" stay fast even though almost no row
--     carries the column. Partial keeps storage tiny.
--
-- Why ON DELETE SET NULL (not CASCADE):
--   • Recipient owns the fork. Source-side deletion must not touch it.
--   • Audit row ``conversation.fork`` already records the source id at
--     fork time, so forensic readers don't need the FK pointer to
--     reconstruct lineage.
--
-- Forward compatibility:
--   • Multi-hop chains form naturally — a fork of a fork sets its own
--     parent_conversation_id to the immediate ancestor; recursive CTE
--     answers "ancestors of X" without a schema change.
--   • The matching ``conversation_shares`` table lands in PR 6.1
--     migration 0023 (kept separate so this PR ships fork-only changes).
--
-- Index creation note: this migration uses CREATE INDEX (in a
-- transaction via yoyo). For tables with significant existing rows in
-- production, operators should pre-create the index concurrently before
-- applying; the IF NOT EXISTS makes the migration a no-op in that case.

ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS forked_from_share_id TEXT;

-- ADD CONSTRAINT IF NOT EXISTS isn't supported by PostgreSQL; guard the
-- ADD with a DO block so re-runs (and stacked migrations on the same
-- already-bootstrapped schema in test fixtures) stay idempotent.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'fk_agent_conversations_parent'
    ) THEN
        ALTER TABLE agent_conversations
            ADD CONSTRAINT fk_agent_conversations_parent
            FOREIGN KEY (parent_conversation_id)
            REFERENCES agent_conversations(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_agent_conversations_parent
    ON agent_conversations (parent_conversation_id)
    WHERE parent_conversation_id IS NOT NULL;
