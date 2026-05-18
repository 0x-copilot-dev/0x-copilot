-- Phase 8 — Agents destination canonical schema.
--
-- This DDL is the single source of truth; the production migration at
-- ``services/backend/migrations/<NNNN>_agents.sql`` is a verbatim copy
-- so the migration runner picks it up at boot.
--
-- Per agents-prd §5.1–§5.4 + cross-audit §1.3 master rule:
--
--   * ``agents`` — one row per live agent record. Tenant-scoped via RLS;
--     UNIQUE (tenant_id, slug) WHERE deleted_at IS NULL so case-pristine
--     slugs are reusable after a tombstone.
--   * ``agent_versions`` — immutable snapshots (P8-A2 owns the routes;
--     this file ships the table because the cascade rules + audit chain
--     reference it).
--   * ``agent_installs`` — per-user install + thin override layer (P8-A3
--     owns the routes; this file ships the table so the cleanup job /
--     cascade behavior is captured up front).
--   * ``agent_audit_events`` — append-only audit chain, same shape as
--     ``project_audit_events`` / ``routine_audit_events``.
--
-- Authorization is service-layer (cross-audit §1.3 — owner-only writes
-- on custom agents; tenant-readable system+community; non-readers see
-- 404 not 403) plus RLS for tenant isolation. The PARTIAL UNIQUE
-- ``custom_must_have_owner`` CHECK pins the §6.2 invariant that every
-- ``origin='custom'`` row has a non-null owner_user_id.

-- ---------------------------------------------------------------------------
-- Agents — one row per live agent record.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agents (
    id                       TEXT         PRIMARY KEY,
    tenant_id                TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    name                     TEXT         NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    slug                     TEXT         NOT NULL CHECK (char_length(slug) BETWEEN 1 AND 80),
    description              TEXT         NOT NULL DEFAULT '' CHECK (char_length(description) <= 400),
    icon_emoji               TEXT         NOT NULL DEFAULT '🤖',
    color_hue                INT          NOT NULL DEFAULT 220 CHECK (color_hue BETWEEN 0 AND 359),
    -- Monotonic counter — bumps only on explicit POST /versions snapshot
    -- (agents-prd §3.2). PATCHes do NOT bump this; auto-snapshot would
    -- produce hundreds of versions and break the version picker.
    version                  INT          NOT NULL DEFAULT 1 CHECK (version >= 1),
    status                   TEXT         NOT NULL DEFAULT 'draft'
                                          CHECK (status IN ('installed','available','disabled','draft')),
    origin                   TEXT         NOT NULL CHECK (origin IN ('system','community','custom')),
    -- LOOSE FK: ON DELETE RESTRICT so accidentally hard-deleting a user
    -- cannot drop their custom agents — admin force-transfer is the
    -- supported reassignment path (mirror of projects-prd §3.5.4).
    owner_user_id            TEXT         REFERENCES users(user_id) ON DELETE RESTRICT,
    instructions             TEXT         NOT NULL DEFAULT '',
    model_id                 TEXT         NOT NULL,
    reasoning_depth          TEXT         NOT NULL CHECK (reasoning_depth IN ('fast','balanced','deep')),
    -- JSONB so we can extend the skill / connector shape later (e.g. a
    -- ``{id, scope}`` tuple) without a column rewrite.
    skills                   JSONB        NOT NULL DEFAULT '[]'::JSONB,
    connectors_default       JSONB        NOT NULL DEFAULT '[]'::JSONB,
    permissions              JSONB        NOT NULL,
    -- Phase 11 Memory hook. NULL today; non-null when Phase 11 lands.
    memory_ref               JSONB,
    -- Provenance: which agent this row was duplicated from (§4.10).
    -- ON DELETE SET NULL: source agent's deletion preserves the fork.
    forked_from_agent_id     TEXT         REFERENCES agents(id) ON DELETE SET NULL,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Soft-delete tombstone; cleanup job in agents/cleanup.py hard-
    -- deletes after the 90-day retention window (agents-prd §5.4).
    deleted_at               TIMESTAMPTZ,
    -- Custom agents MUST carry an owner; system/community MUST NOT.
    -- The invariant is enforced at write time by the service layer and
    -- repeated here as the load-bearing CHECK.
    CONSTRAINT custom_must_have_owner CHECK (
        (origin = 'custom'   AND owner_user_id IS NOT NULL)
        OR
        (origin <> 'custom'  AND owner_user_id IS NULL)
    )
);

-- Slug uniqueness scoped to (tenant_id, live-rows-only) so a tombstoned
-- agent's slug can be reused (mirror of projects' name-uniqueness shape).
CREATE UNIQUE INDEX IF NOT EXISTS agents_tenant_slug_unique
    ON agents (tenant_id, slug)
    WHERE deleted_at IS NULL;

-- Hot paths — list/filter axes.
CREATE INDEX IF NOT EXISTS agents_tenant_status_idx
    ON agents (tenant_id, status)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS agents_tenant_origin_idx
    ON agents (tenant_id, origin)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS agents_tenant_owner_idx
    ON agents (tenant_id, owner_user_id)
    WHERE deleted_at IS NULL AND owner_user_id IS NOT NULL;

-- Full-text search (the ``q`` filter axis).
CREATE INDEX IF NOT EXISTS agents_search_idx
    ON agents USING GIN (to_tsvector('simple', name || ' ' || coalesce(description,'') || ' ' || slug))
    WHERE deleted_at IS NULL;

ALTER TABLE agents ENABLE ROW LEVEL SECURITY;

CREATE POLICY agents_tenant_isolation ON agents
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Agent versions — immutable snapshots (P8-A2 routes ride on this table).
-- ---------------------------------------------------------------------------
--
-- ON DELETE RESTRICT on agent_id: soft-deleting an agent leaves versions
-- intact (Routines pinning a version still resolve). Hard delete cascades
-- via the cleanup job per agents-prd §5.4 (only when no Routines pin
-- the agent's versions).

CREATE TABLE IF NOT EXISTS agent_versions (
    id                                TEXT         PRIMARY KEY,
    agent_id                          TEXT         NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    tenant_id                         TEXT         NOT NULL,
    version                           INT          NOT NULL CHECK (version >= 1),
    instructions_snapshot             TEXT         NOT NULL,
    model_id_snapshot                 TEXT         NOT NULL,
    reasoning_depth_snapshot          TEXT         NOT NULL CHECK (reasoning_depth_snapshot IN ('fast','balanced','deep')),
    skills_snapshot                   JSONB        NOT NULL,
    connectors_default_snapshot       JSONB        NOT NULL,
    permissions_snapshot              JSONB        NOT NULL,
    label                             TEXT,
    created_at                        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- LOOSE FK: keep the snapshot's authorship even when the creating
    -- user is hard-deleted.
    created_by                        TEXT         NOT NULL,
    UNIQUE (agent_id, version)
);

CREATE INDEX IF NOT EXISTS agent_versions_tenant_idx
    ON agent_versions (tenant_id, agent_id, version DESC);

ALTER TABLE agent_versions ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_versions_tenant_isolation ON agent_versions
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Agent installs — per-user installation + thin override layer.
-- ---------------------------------------------------------------------------
--
-- The (tenant_id, agent_id, user_id) tuple is the natural key. The
-- surrogate ``id`` lets audit-row targets name the row directly. Disabling
-- is a flag flip, NOT a row delete — so the user's override layer is
-- preserved across enable/disable cycles.

CREATE TABLE IF NOT EXISTS agent_installs (
    id                     TEXT         PRIMARY KEY,
    tenant_id              TEXT         NOT NULL,
    agent_id               TEXT         NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id                TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    installed_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    disabled               BOOLEAN      NOT NULL DEFAULT FALSE,
    overrides              JSONB,
    -- Optional version pin. NULL = live (auto-upgrade on snapshots).
    pinned_version_id      TEXT         REFERENCES agent_versions(id) ON DELETE SET NULL,
    UNIQUE (tenant_id, agent_id, user_id)
);

CREATE INDEX IF NOT EXISTS agent_installs_user_idx
    ON agent_installs (tenant_id, user_id);

CREATE INDEX IF NOT EXISTS agent_installs_agent_idx
    ON agent_installs (tenant_id, agent_id);

CREATE INDEX IF NOT EXISTS agent_installs_disabled_idx
    ON agent_installs (tenant_id, user_id, disabled);

ALTER TABLE agent_installs ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_installs_tenant_isolation ON agent_installs
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Agent audit events — append-only; CRUD + install + version snapshot
-- all funnel through this table. Same shape as project_audit_events /
-- routine_audit_events / todo_audit_events.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Dotted action taxonomy per agents-prd §6.1:
    --   agent.create / agent.update / agent.soft_delete /
    --   agent.hard_delete / agent.install / agent.install_tenant /
    --   agent.uninstall / agent.disable / agent.enable /
    --   agent.version_snapshot / agent.duplicate / agent.status_change
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'agent',
    target_id           TEXT         NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    -- cross-audit §1.4 ``context`` field.
    context             JSONB,
    correlation_id      TEXT,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- audit-chain integration; same shape as project_audit_events.
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS agent_audit_tenant_idx
    ON agent_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS agent_audit_target_idx
    ON agent_audit_events (tenant_id, target_id, ts);

CREATE INDEX IF NOT EXISTS agent_audit_correlation_idx
    ON agent_audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

ALTER TABLE agent_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_audit_tenant_isolation ON agent_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Grants — same idiom as the projects / routines / todos / inbox schema
-- files.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON agents TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, DELETE ON agent_versions TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON agent_installs TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON agent_audit_events TO enterprise_app';
    END IF;
END
$$;
