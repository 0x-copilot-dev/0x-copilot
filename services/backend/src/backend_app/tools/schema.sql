-- Phase 10 — Tools destination canonical schema.
--
-- Source: docs/atlas-new-design/destinations/tools-prd.md §5.1.
-- This DDL is the single source of truth for the `tools` catalog table;
-- the production migration at
-- `services/backend/migrations/<NNNN>_tools.sql` is a verbatim copy so
-- the migration runner picks it up at boot.
--
-- The `runtime_tool_invocations` table (already exists; Phase 0) is the
-- per-call audit + usage source. Phase 10 reuses it as-is and adds two
-- indexes for the invocations-tab + error-rate aggregation paths.
--
-- Authorization is service-layer (tools-prd §6 + cross-audit §1.3 master
-- rule): tenant-scoped via RLS, project-scoped reads via the canonical
-- `is_project_member` resolver, owner-or-admin writes. Non-readers see
-- 404 (existence not leaked) — never 403 for an out-of-scope read.

-- ---------------------------------------------------------------------------
-- Tools — one row per live tool catalog entry.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tools (
    id                          TEXT         PRIMARY KEY,
    tenant_id                   TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    name                        TEXT         NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    description                 TEXT         NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
    kind                        TEXT         NOT NULL
                                              CHECK (kind IN ('mcp','openapi','builtin','code','skill')),
    scope                       TEXT         NOT NULL CHECK (scope IN ('read','write','both')),
    status                      TEXT         NOT NULL DEFAULT 'enabled'
                                              CHECK (status IN ('enabled','disabled','error','pending_review')),
    status_reason               TEXT,
    -- JSON Schemas (Draft 2020-12) — server-validated at call time.
    args_schema                 JSONB        NOT NULL DEFAULT '{}'::JSONB,
    returns_schema              JSONB        NOT NULL DEFAULT '{}'::JSONB,
    -- `ToolTransport` shape; kind discriminator inside the blob.
    transport                   JSONB        NOT NULL,
    -- LOOSE FK: ON DELETE RESTRICT so hard-deleting a user does not drop
    -- their custom tools; admin force-transfer is the supported path
    -- (mirror of the agents-prd / projects-prd ownership invariant).
    owner_user_id               TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Optional filing axis. When non-null the master ACL rule applies.
    project_id                  TEXT         REFERENCES projects(id) ON DELETE SET NULL,
    -- Back-link to a Library page (kind=skill).
    skill_page_ref              JSONB,
    -- Code-routine shape (kind=code) — { repo_ref, env_ref, entry }.
    code_ref                    JSONB,
    tags                        TEXT[]       NOT NULL DEFAULT '{}',
    -- Auto-bumps per consecutive transport-level failure; flips status to
    -- 'error' on threshold; cleared on first success. Bumped by
    -- ai-backend via POST /internal/v1/tools/{id}/error.
    consecutive_error_count     INT          NOT NULL DEFAULT 0
                                              CHECK (consecutive_error_count >= 0),
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Soft-delete tombstone; cleanup job hard-deletes after the 90-day
    -- retention window (tools-prd §5.4).
    deleted_at                  TIMESTAMPTZ,
    -- skill rows MUST carry skill_page_ref; code rows MUST carry code_ref.
    CONSTRAINT skill_must_have_page_ref CHECK (
        kind <> 'skill' OR skill_page_ref IS NOT NULL
    ),
    CONSTRAINT code_must_have_code_ref CHECK (
        kind <> 'code' OR code_ref IS NOT NULL
    )
);

-- Hot paths — list/filter axes per tools-prd §5.1.
CREATE INDEX IF NOT EXISTS tools_tenant_kind_idx
    ON tools (tenant_id, deleted_at, kind)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS tools_tenant_project_idx
    ON tools (tenant_id, project_id, deleted_at)
    WHERE deleted_at IS NULL AND project_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS tools_tenant_owner_idx
    ON tools (tenant_id, owner_user_id, deleted_at)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS tools_tenant_status_idx
    ON tools (tenant_id, status)
    WHERE deleted_at IS NULL;

-- Tag filter axis.
CREATE INDEX IF NOT EXISTS tools_tags_gin_idx
    ON tools USING GIN (tags)
    WHERE deleted_at IS NULL;

-- Free-text search (q filter) + name sort (name:asc).
CREATE INDEX IF NOT EXISTS tools_lower_name_idx
    ON tools (lower(name))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS tools_search_idx
    ON tools USING GIN (to_tsvector('simple', name || ' ' || coalesce(description,'')))
    WHERE deleted_at IS NULL;

ALTER TABLE tools ENABLE ROW LEVEL SECURITY;

CREATE POLICY tools_tenant_isolation ON tools
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Tool audit events — append-only; CRUD + status changes + test-call
-- writes funnel through this table. Same shape as project_audit_events /
-- agent_audit_events / routine_audit_events (tools-prd §6.3).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tool_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Dotted action taxonomy per tools-prd §6.3:
    --   tool.created / tool.updated / tool.disabled / tool.enabled /
    --   tool.deleted / tool.purged / tool.test_called /
    --   tool.scope_changed / tool.error_threshold
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'tool',
    target_id           TEXT         NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    -- cross-audit §1.4 `context` field.
    context             JSONB,
    correlation_id      TEXT,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- audit-chain integration; same shape as project_audit_events.
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS tool_audit_tenant_idx
    ON tool_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS tool_audit_target_idx
    ON tool_audit_events (tenant_id, target_id, ts);

CREATE INDEX IF NOT EXISTS tool_audit_correlation_idx
    ON tool_audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

ALTER TABLE tool_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY tool_audit_tenant_isolation ON tool_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- runtime_tool_invocations — already exists (Phase 0). Phase 10 adds two
-- indexes for the invocations-tab + error-rate aggregation paths. The
-- index names use `IF NOT EXISTS` so a pre-existing index name does not
-- block the migration.
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS runtime_tool_invocations_tenant_tool_idx
    ON runtime_tool_invocations (tenant_id, tool_id, started_at DESC);

CREATE INDEX IF NOT EXISTS runtime_tool_invocations_tenant_tool_status_idx
    ON runtime_tool_invocations (tenant_id, tool_id, status, started_at DESC);


-- ---------------------------------------------------------------------------
-- Grants — same idiom as projects / routines / agents / inbox.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON tools TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON tool_audit_events TO enterprise_app';
    END IF;
END
$$;
