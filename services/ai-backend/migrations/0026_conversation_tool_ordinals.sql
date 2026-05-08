-- PR 04 (citations binding map): persistent ordinal ↔ tool_call_id map.
--
-- Background: model-declared citations ([[N]] markers) need a stable
-- conversation-scoped pointer. Previously the conversation_ordinal counter
-- lived only in-memory in the ``ConversationOrdinalAllocator`` and was
-- re-derived on resume by counting ``TOOL_CALL_STARTED`` events. That
-- positional re-derivation diverges from the live counter when the MCP
-- middleware allocates inside a tool body, when approval interrupts cause
-- repeated re-binds, or when subagents allocate in parallel. The result
-- is ordinal collisions across runs in one conversation — ordinal 5 ends
-- up bound to two different tool_call_ids depending on which counter you
-- ask. See ``docs/new-design/04-citations-binding-map.md`` §1.1.
--
-- This migration adds the canonical binding table. After the allocator
-- refactor in Phase 3 lands, every ordinal allocation writes one row here
-- and the allocator restores its state from this table on bind.
--
-- Storage shape:
--   PRIMARY KEY (conversation_id, conversation_ordinal) — the ordinal is
--     conversation-scoped by definition; the same ordinal value in two
--     different conversations is a different binding.
--   UNIQUE (conversation_id, tool_call_id) — the binding is bidirectional;
--     a retried allocate for the same tool_call_id (e.g. LangGraph
--     re-dispatch after an approval pause) must collapse to the same
--     ordinal. The allocator's UPSERT relies on this constraint.
--   org_id mirrored onto the row for RLS parity with every other
--     tenant-scoped table; the FK to agent_conversations carries tenancy
--     too but RLS policies key off the row column directly.
--   ON DELETE CASCADE — bindings live and die with the conversation. No
--     standalone TTL or revocation primitive.

CREATE TABLE IF NOT EXISTS agent_conversation_tool_ordinals (
    org_id               TEXT         NOT NULL,
    conversation_id      TEXT         NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    conversation_ordinal INTEGER      NOT NULL CHECK (conversation_ordinal > 0),
    tool_call_id         TEXT         NOT NULL,
    tool_name            TEXT         NOT NULL,
    run_id               TEXT         NOT NULL,
    allocated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (conversation_id, conversation_ordinal),
    UNIQUE (conversation_id, tool_call_id)
);

-- Hot path: the cross-turn observation builder loads all bindings for one
-- conversation at the start of every new run. The PK already covers that;
-- this index is for the secondary "what did this run allocate?" query
-- used by debug tooling and the optional backfill audit.
CREATE INDEX IF NOT EXISTS idx_actio_conversation_run
    ON agent_conversation_tool_ordinals (conversation_id, run_id);

-- RLS — same shape as every other tenant-scoped table (see migration 0008).
-- Cross-tenant reads physically impossible once the runtime adapter sets
-- ``app.current_org`` on connection checkout.
ALTER TABLE agent_conversation_tool_ordinals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_conversation_tool_ordinals
    USING (org_id = current_setting('app.current_org', true));
