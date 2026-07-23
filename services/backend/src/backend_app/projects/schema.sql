-- Phase 6 — Projects destination canonical schema.
--
-- The DDL below is the single source of truth; the migration file at
-- ``services/backend/migrations/<NNNN>_projects.sql`` is a verbatim copy
-- so the migration runner picks it up at boot.
--
-- Authorization is service-layer (cross-audit §1.3 / projects-prd §7 —
-- owner writes, project-member reads, admin compliance reads,
-- 404-not-403) plus RLS for tenant isolation. The canonical membership
-- predicate ``is_member`` lives at backend_app/projects/acl.py and is
-- consumed by every other destination (Todos / Inbox / Routines /
-- Library / Memory / Chats) via in-process import; ``ai-backend``
-- consumes it via ``/internal/v1/projects/{id}/membership/{user_id}``.

-- ---------------------------------------------------------------------------
-- Projects — one row per project.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Phase 6.5 §5 — ``default_connector_allowlist`` JSONB column added below.
-- ``NULL`` = inherit owner defaults; ``[]`` = explicit deny;
-- ``["salesforce", "gmail", ...]`` = allowlist of ConnectorSlug kinds.
-- JSONB (not text[]) so we can extend to ``{slug, scope}`` later without
-- a column rewrite (projects-extensions §5.2).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    -- The owner FK is LOOSE — owner_offboarded is handled via the
    -- daily cron (projects-prd §3.5.4) which fires Inbox CTAs to
    -- tenant admins. ON DELETE RESTRICT prevents accidental cascade
    -- of every project when a user is hard-deleted; admin force-
    -- transfer (projects-prd §12 Q1) is the supported reassignment
    -- path.
    owner_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    name                TEXT         NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    description         TEXT         NOT NULL DEFAULT '' CHECK (char_length(description) <= 400),
    icon_emoji          TEXT         NOT NULL DEFAULT '📁',
    color_hue           INT          NOT NULL DEFAULT 210 CHECK (color_hue BETWEEN 0 AND 359),
    status              TEXT         NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    -- Present iff status='archived'. The CHECK below pins the invariant
    -- so the service layer and any external writer share the same
    -- guarantee.
    archived_at         TIMESTAMPTZ,
    -- Denormalized: advanced by the activity projector on every
    -- ``project_activity`` insert. The nightly reconciliation job
    -- (projects-prd §5.4) repairs drift.
    last_activity_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Soft-delete marker; cleanup job in projects/jobs hard-deletes
    -- after the 30-day retention window (projects-prd §5.3).
    deleted_at          TIMESTAMPTZ,
    -- Phase 6.5 §5.2 — connector inheritance.
    default_connector_allowlist  JSONB DEFAULT NULL,
    CONSTRAINT projects_archived_at_invariant CHECK (
        (status = 'archived'   AND archived_at IS NOT NULL)
        OR
        (status = 'active'     AND archived_at IS NULL)
    )
);

-- Case-insensitive duplicate-name guard, scoped to live (non-deleted)
-- rows so a tombstoned project's name can be reused.
CREATE UNIQUE INDEX IF NOT EXISTS projects_tenant_name_unique
    ON projects (tenant_id, lower(name))
    WHERE deleted_at IS NULL;

-- Hot path: primary list query — order by recency within an active set.
CREATE INDEX IF NOT EXISTS projects_tenant_status_idx
    ON projects (tenant_id, status, last_activity_at DESC NULLS LAST)
    WHERE deleted_at IS NULL;

-- Owner-filter path.
CREATE INDEX IF NOT EXISTS projects_owner_idx
    ON projects (tenant_id, owner_user_id)
    WHERE deleted_at IS NULL;

-- Search path: ``q=…`` runs ``plainto_tsquery('simple', q)`` against
-- ``name || ' ' || description``. The GIN index covers the predicate.
CREATE INDEX IF NOT EXISTS projects_search_idx
    ON projects USING GIN (to_tsvector('simple', name || ' ' || description))
    WHERE deleted_at IS NULL;

-- Tenant isolation via RLS — second wall behind the application-side
-- WHERE clause. Same policy shape as inbox / todos / routines.
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY projects_tenant_isolation ON projects
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Project memberships — composite-PK row per (project, user).
-- ---------------------------------------------------------------------------
--
-- The PARTIAL UNIQUE on ``(project_id) WHERE role='owner'`` is the
-- load-bearing invariant: exactly ONE owner row per project at any
-- moment. Ownership transfer is an atomic two-step (demote old owner,
-- promote new owner) executed in a single transaction so no reader
-- ever sees zero or two owners.
--
-- ``ON DELETE CASCADE`` on both FKs: a hard-deleted project drops its
-- membership rows; a hard-deleted user drops their membership rows
-- (the project's owner pointer stays — admin force-transfer is the
-- recovery path; projects-prd §3.5.4).

CREATE TABLE IF NOT EXISTS project_memberships (
    project_id      TEXT         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id         TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    tenant_id       TEXT         NOT NULL,
    role            TEXT         NOT NULL CHECK (role IN ('owner','editor','viewer')),
    added_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- LOOSE FK: keep the audit trail even when the adding user is
    -- hard-deleted.
    added_by        TEXT         NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

-- Exactly one owner per project (the canonical invariant).
CREATE UNIQUE INDEX IF NOT EXISTS project_memberships_owner_partial_unique
    ON project_memberships (project_id)
    WHERE role = 'owner';

-- "What projects am I in?" — hot path for the rail / panel.
CREATE INDEX IF NOT EXISTS project_memberships_user_idx
    ON project_memberships (tenant_id, user_id);

-- Members of a project — paginate the Members tab.
CREATE INDEX IF NOT EXISTS project_memberships_project_idx
    ON project_memberships (project_id);

ALTER TABLE project_memberships ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_memberships_tenant_isolation ON project_memberships
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Per-user star.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_stars (
    tenant_id       TEXT         NOT NULL,
    user_id         TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_id      TEXT         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id, project_id)
);

CREATE INDEX IF NOT EXISTS project_stars_user_idx
    ON project_stars (tenant_id, user_id);

ALTER TABLE project_stars ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_stars_tenant_isolation ON project_stars
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Projected activity stream — the audit-row projection keyed by
-- project_id (projects-prd §3.7). The projector worker lives at
-- backend_app/projects/activity_projector.py (out of scope for P6-A1 —
-- ships alongside the cross-destination activity feed).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_activity (
    id                  TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL,
    project_id          TEXT         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- Idempotency key — UNIQUE per (tenant, audit_id) so replaying
    -- the same audit event does not double-project. cross-audit §1.4.
    audit_id            TEXT         NOT NULL,
    actor_user_id       TEXT,
    -- Denormalized at projection time. A small daily cron refreshes
    -- this for the past 24h (projects-prd §5.4 step 5); deeper
    -- history retains the historical name — a feature for forensics.
    actor_display_name  TEXT         NOT NULL DEFAULT '',
    action              TEXT         NOT NULL,
    kind                TEXT         NOT NULL,
    ref_kind            TEXT         NOT NULL,
    ref_id              TEXT         NOT NULL,
    preview             TEXT         NOT NULL DEFAULT '' CHECK (char_length(preview) <= 200),
    occurred_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS project_activity_audit_idx
    ON project_activity (tenant_id, audit_id);

CREATE INDEX IF NOT EXISTS project_activity_project_time_idx
    ON project_activity (tenant_id, project_id, occurred_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS project_activity_kind_idx
    ON project_activity (tenant_id, project_id, kind, occurred_at DESC);

ALTER TABLE project_activity ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_activity_tenant_isolation ON project_activity
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Per-project counts are COMPUTED ON READ (PRD-07), not stored.
--
-- The former per-project rollup counter cache never had a writer, so every row
-- read as zero. It is dropped (migration 0047). The projects service now asks
-- each destination's owning service for a grouped `count_by_project`, so the
-- number can never disagree with the list it summarizes; `chats` is filled by
-- the facade from ai-backend. There is no counter table here anymore.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- Audit events — append-only; CRUD + membership + transfer all funnel
-- through this table. Same shape as routine_audit_events /
-- todo_audit_events / inbox_audit_events.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    -- Dotted action taxonomy per projects-prd §6.1:
    --   project.created / project.updated / project.deleted /
    --   project.restored / project.archived / project.activated /
    --   project.member_added / project.member_removed /
    --   project.member_role_changed / project.ownership_transferred /
    --   project.admin_force_transferred / project.starred /
    --   project.unstarred / project.compliance_read /
    --   project.activity_reconciled / project.retention_cleanup_run
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'project',
    target_id           TEXT         NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    -- cross-audit §1.4 ``context`` field — carries project_id and
    -- transfer-specific (from/to user) details.
    context             JSONB,
    correlation_id      TEXT,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- audit-chain integration; same shape as todo_audit_events.
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS project_audit_tenant_idx
    ON project_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS project_audit_target_idx
    ON project_audit_events (tenant_id, target_id, ts);

CREATE INDEX IF NOT EXISTS project_audit_correlation_idx
    ON project_audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

ALTER TABLE project_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_audit_tenant_isolation ON project_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Phase 6.5 §7.7 — Project templates. Tenant-scoped, soft-deleted, with
-- an immutable snapshot blob. Forking is a COPY (no FK back to template).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_templates (
    id                  TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    owner_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    name                TEXT         NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    description         TEXT         NOT NULL DEFAULT '' CHECK (char_length(description) <= 200),
    snapshot            JSONB        NOT NULL,
    -- LOOSE FK: keep the template even when the source project is hard-
    -- deleted (templates outlive their source per §7.2).
    source_project_id   TEXT         NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS project_templates_tenant_idx
    ON project_templates (tenant_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS project_templates_owner_idx
    ON project_templates (tenant_id, owner_user_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS project_templates_search_idx
    ON project_templates
    USING GIN (to_tsvector('simple', name || ' ' || description))
    WHERE deleted_at IS NULL;

ALTER TABLE project_templates ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_templates_tenant_isolation ON project_templates
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));


-- ---------------------------------------------------------------------------
-- Grants — same idiom as the routines / todos / inbox schema files.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON projects TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON project_memberships TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, DELETE ON project_stars TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON project_activity TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON project_audit_events TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON project_templates TO enterprise_app';
    END IF;
END
$$;
