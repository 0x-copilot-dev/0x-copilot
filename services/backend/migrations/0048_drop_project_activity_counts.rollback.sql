-- Rollback for 0048_drop_project_activity_counts.sql.
--
-- Recreates `project_activity_counts` verbatim from 0043_projects.sql
-- (table + RLS policy + grant). The table is inert on restore — PRD-07
-- deleted the code path that read it — so a rollback of the migration alone
-- is a no-op cache; the `service.py` computed-on-read commit must be reverted
-- alongside it for the old behavior to return.

CREATE TABLE IF NOT EXISTS project_activity_counts (
    tenant_id           TEXT         NOT NULL,
    project_id          TEXT         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chats               INT          NOT NULL DEFAULT 0,
    todos_open          INT          NOT NULL DEFAULT 0,
    todos_done          INT          NOT NULL DEFAULT 0,
    inbox_items         INT          NOT NULL DEFAULT 0,
    library_items       INT          NOT NULL DEFAULT 0,
    routines_active     INT          NOT NULL DEFAULT 0,
    members             INT          NOT NULL DEFAULT 0,
    recomputed_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id)
);

ALTER TABLE project_activity_counts ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_activity_counts_tenant_isolation ON project_activity_counts
    USING (
        tenant_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON project_activity_counts TO enterprise_app';
    END IF;
END
$$;
