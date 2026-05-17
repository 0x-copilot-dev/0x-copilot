-- Phase 3 — Todos destination canonical schema. The DDL below is the
-- single source of truth; the migration file at
-- ``services/backend/migrations/0032_todos.sql`` is a verbatim copy so
-- the migration runner picks it up at boot.
--
-- Authorization is service-layer (cross-audit §1.3) plus RLS for
-- tenant isolation; project membership lookup composes with this
-- table at the service layer (no FK to a projects table yet — Phase
-- 6+ ships ``project_members``).

CREATE TABLE IF NOT EXISTS todos (
    id                            TEXT         PRIMARY KEY,
    tenant_id                     TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    owner_user_id                 TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_id                    TEXT,                                              -- nullable; no FK until Projects ships
    text                          TEXT         NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
    status                        TEXT         NOT NULL DEFAULT 'open'
                                  CHECK (status IN ('open','done')),
    priority                      TEXT         NOT NULL DEFAULT 'med'
                                  CHECK (priority IN ('low','med','high')),
    due                           TIMESTAMPTZ,
    source                        JSONB        NOT NULL DEFAULT '{"kind":"user"}'::jsonb,
    -- One-level subtasks (impl-plan §11.2). FK with cascade-delete
    -- means deleting a parent removes its children; a CHECK constraint
    -- below prevents nested subtasks at the DB level (the service
    -- layer also rejects with 400 — defense in depth).
    parent_id                     TEXT         REFERENCES todos(id) ON DELETE CASCADE,
    sort_index_within_parent      DOUBLE PRECISION,
    -- Recurrence (impl-plan §11.1). ``series_id`` shared across every
    -- concrete instance; the materialiser worker uses the partial
    -- unique index below to dedupe ``(series_id, due)`` re-fires.
    recurrence                    JSONB,
    series_id                     TEXT,
    created_at                    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at                  TIMESTAMPTZ,
    deleted_at                    TIMESTAMPTZ
);

-- Hot path: per-tenant open queue, newest first.
CREATE INDEX IF NOT EXISTS todos_tenant_status_idx
    ON todos (tenant_id, status, created_at DESC)
    WHERE deleted_at IS NULL;

-- Project-scoped reads (cross-audit §1.3 — project-member visibility).
CREATE INDEX IF NOT EXISTS todos_tenant_project_idx
    ON todos (tenant_id, project_id, created_at DESC)
    WHERE deleted_at IS NULL;

-- Subtask listings under a single parent (UI nesting).
CREATE INDEX IF NOT EXISTS todos_tenant_parent_idx
    ON todos (tenant_id, parent_id)
    WHERE parent_id IS NOT NULL AND deleted_at IS NULL;

-- Materialiser dedup. ``(series_id, due)`` is unique only when both
-- are non-null, so non-recurring rows aren't affected.
CREATE UNIQUE INDEX IF NOT EXISTS todo_series_dedup
    ON todos (series_id, due)
    WHERE series_id IS NOT NULL AND due IS NOT NULL;

-- Tenant isolation via RLS — matches the policy on every product
-- table.
ALTER TABLE todos ENABLE ROW LEVEL SECURITY;

CREATE POLICY todos_tenant_isolation ON todos
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

-- ---------------------------------------------------------------------------
-- Series table (one row per recurring sequence; the materialiser advances
-- ``last_materialized_due`` after each successful insert).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS todo_series (
    id                       TEXT         PRIMARY KEY,
    tenant_id                TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    owner_user_id            TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    rule                     TEXT         NOT NULL,
    spec                     TEXT         NOT NULL,
    started_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ends_at                  TIMESTAMPTZ,
    last_materialized_due    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS todo_series_tenant_idx
    ON todo_series (tenant_id, owner_user_id);

ALTER TABLE todo_series ENABLE ROW LEVEL SECURITY;

CREATE POLICY todo_series_tenant_isolation ON todo_series
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

-- ---------------------------------------------------------------------------
-- Audit events — append-only; bulk actions stamp a shared correlation_id
-- across every row written by the same bulk write (Todos PRD §6).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS todo_audit_events (
    audit_id            TEXT         PRIMARY KEY,
    tenant_id           TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE RESTRICT,
    actor_user_id       TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    action              TEXT         NOT NULL,
    target_kind         TEXT         NOT NULL DEFAULT 'todo',
    target_id           TEXT         NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    correlation_id      TEXT,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    seq                 BIGINT,
    prev_hash           BYTEA,
    signature           BYTEA,
    key_version         INTEGER
);

CREATE INDEX IF NOT EXISTS todo_audit_tenant_idx
    ON todo_audit_events (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS todo_audit_correlation_idx
    ON todo_audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS todo_audit_target_idx
    ON todo_audit_events (tenant_id, target_id, ts);

ALTER TABLE todo_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY todo_audit_tenant_isolation ON todo_audit_events
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON todos TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON todo_series TO enterprise_app';
        EXECUTE 'GRANT SELECT, INSERT ON todo_audit_events TO enterprise_app';
    END IF;
END
$$;
