-- PR B1: per-workspace + per-user tool-use policy.
--
-- Three policy axes — `read`, `write`, `destructive` — each with one
-- of four modes: `auto` (silently allowed), `ask` (one-time prompt),
-- `require` (always prompt), `block` (never allowed).
--
-- Workspace default and per-user override live in the same table:
--   * `user_id IS NULL`  → workspace default
--   * `user_id IS NOT NULL` → user override (wins for that user)
--
-- A composite unique index on `(org_id, COALESCE(user_id, '__org__'),
-- kind)` enforces "exactly one row per (scope, kind)". Postgres can't
-- index NULL through a unique constraint, so we coalesce to a
-- sentinel string at index time.
--
-- The policy *evaluator* lives in the AI backend's
-- ``ToolPermissionChecker`` (capabilities/tools/permissions.py).
-- This table is the source of truth; the evaluator fetches the
-- policy once per run start and caches it on AgentRuntimeContext.

CREATE TABLE IF NOT EXISTS tool_use_policies (
    org_id              TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    user_id             TEXT,
    kind                TEXT         NOT NULL CHECK (kind IN ('read', 'write', 'destructive')),
    mode                TEXT         NOT NULL CHECK (mode IN ('auto', 'ask', 'require', 'block')),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by_user_id  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_tool_use_policies_scope_kind
    ON tool_use_policies (org_id, COALESCE(user_id, '__org__'), kind);

CREATE INDEX IF NOT EXISTS idx_tool_use_policies_org_user
    ON tool_use_policies (org_id, user_id);

ALTER TABLE tool_use_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tool_use_policies
    USING (org_id = current_setting('app.current_org', true));
