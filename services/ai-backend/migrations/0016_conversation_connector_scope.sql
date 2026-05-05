-- PR 1.2: per-chat connector scope persistence.
--
-- Adds a JSONB override on agent_conversations capturing which connectors
-- this chat may use, with what scopes. Read at run-create when the inbound
-- request omits the x-enterprise-connector-scopes header; ignored when the
-- header is present (header wins; service-to-service callers stay in
-- control). Mid-run mutation is intentionally not honoured — the worker
-- builds capabilities from agent_runs.runtime_context_json which is frozen
-- at run start (see docs/architecture/runtime-stream-handshake.md).
--
-- Storage shape inside the column:
--   { "<connector_id>": ["scope_a","scope_b"]   -- active with these scopes
--   , "<connector_id>": null                     -- paused for this chat
--   , ...                                        -- omitted = no override
--   }
--
-- Default '{}' means "no per-chat override" — the run-create fallback uses
-- the inbound header (or empty set) verbatim. Last-write-wins on the
-- column; the audit row carries the diff for forensic reconstruction
-- (see RuntimeApiService.update_conversation_connectors).
--
-- Note on index creation: this migration uses CREATE INDEX (in a tx via
-- yoyo) rather than CREATE INDEX CONCURRENTLY. For tables with significant
-- existing rows in production, operators should pre-create the index
-- concurrently before applying this migration; the IF NOT EXISTS makes the
-- migration a no-op in that case.

ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS enabled_connectors JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS connectors_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_conversations_enabled_connectors
    ON agent_conversations USING gin (enabled_connectors jsonb_path_ops);
