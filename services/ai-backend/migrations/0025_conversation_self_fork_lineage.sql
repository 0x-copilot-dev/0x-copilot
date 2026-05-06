-- PR A3: self-fork lineage.
--
-- Completes the fork primitives so a user can fork *their own*
-- conversation from a specific message ("retry from here" / "fork to
-- new chat"). PR 6.2 added share-based forks via
-- ``forked_from_share_id``; this PR slots a sibling pointer for the
-- non-share path.
--
-- One nullable column on ``agent_conversations``:
--   * ``forked_from_message_id`` — the source message id the user
--     forked from. NULL on every non-self-fork row. No FK: messages
--     are soft-deletable independently of the fork they spawned, and
--     the audit row ``conversation.self_fork`` records the id at fork
--     time for forensic reconstruction.
--
-- ``parent_conversation_id`` (PR 1.6, migration 0020) covers the
-- conversation-level lineage. The two pointers together let queries
-- like "all forks of X starting from message Y" stay direct.
--
-- Why no FK to messages: a message can be soft-deleted (PR 1.6) or
-- hard-pruned by retention before the fork is closed; the fork must
-- survive both. Audit chain holds the canonical pointer.

ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS forked_from_message_id TEXT;

-- Sparse index — almost no row carries the column, so partial index
-- keeps storage tiny while still answering "all forks from message Y".
CREATE INDEX IF NOT EXISTS idx_agent_conversations_forked_from_message
    ON agent_conversations (forked_from_message_id)
    WHERE forked_from_message_id IS NOT NULL;
