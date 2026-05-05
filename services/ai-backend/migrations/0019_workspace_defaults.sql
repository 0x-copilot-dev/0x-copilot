-- PR 1.6: workspace-level runtime defaults.
--
-- Reads at conversation-create + run-create when the inbound request
-- omits the corresponding field. One row per org. The Settings →
-- Workspace panel writes this row; admins authoring via
-- ``POST /v1/agent/workspace/defaults`` carry the existing
-- ``ADMIN_USERS`` permission scope.
--
-- Storage shape:
--   default_model       JSONB:
--     { "provider": "openai",
--       "model_name": "gpt-5.4-mini",
--       "reasoning":  { "effort": "medium" } | null }
--
--   default_connectors  JSONB (mirrors agent_conversations.enabled_connectors):
--     { "<connector_id>": ["scope_a", ...]      -- active for new chats
--     , "<connector_id>": null                   -- installed but off by default
--     }
--
-- Deliberately *no* retention column — retention storage is migration
-- 0012 ``retention_policies`` (one row per scope/kind). The Settings
-- retention slider composes a ``scope='org'`` row across the relevant
-- kinds inside ``WorkspaceDefaultsService.update`` so the user sees one
-- atomic write. See docs/new-design/pr-1.6-...md §3.4.

CREATE TABLE IF NOT EXISTS workspace_defaults (
    org_id              TEXT PRIMARY KEY,
    default_model       JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_connectors  JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_user_id  TEXT
);

-- RLS: same pattern as migration 0008. The runtime API sets
-- ``app.current_org`` per connection; cross-tenant reads are physically
-- impossible.
ALTER TABLE workspace_defaults ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON workspace_defaults
    USING (org_id = current_setting('app.current_org', true));
