-- Rollback for PR 6.2 — fork lineage.
--
-- Drops the FK self-reference, the sparse index, and the new
-- forked_from_share_id column. Existing forked rows lose the audit
-- pointer; the conversation rows themselves remain intact (they are
-- normal owned conversations).

DROP INDEX IF EXISTS idx_agent_conversations_parent;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'fk_agent_conversations_parent'
    ) THEN
        ALTER TABLE agent_conversations
            DROP CONSTRAINT fk_agent_conversations_parent;
    END IF;
END
$$;

ALTER TABLE agent_conversations
    DROP COLUMN IF EXISTS forked_from_share_id;
