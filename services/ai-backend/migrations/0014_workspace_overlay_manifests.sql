-- 0014_workspace_overlay_manifests — C1 durable run-scoped workspace overlays.
--
-- One manifest row is the optimistic-CAS boundary for an agent run's virtual
-- workspace. It holds redacted metadata only: canonical virtual paths,
-- immutable artifact references/digests, preconditions, and stage bindings.
-- It never holds workspace bytes, native host paths, or a C2 commit permit.
--
-- ``WorkspaceOverlayStorePort`` is run-scoped, so the worker derives the org
-- from ``agent_runs`` rather than accepting a caller-controlled tenant id. The
-- composite FK makes that scope durable and cascades an approved account
-- re-key without a second mutable write path.

ALTER TABLE agent_runs
    ADD CONSTRAINT agent_runs_org_id_id_key UNIQUE (org_id, id);

CREATE TABLE runtime_workspace_overlay_manifests (
    org_id          text NOT NULL,
    run_id          text NOT NULL,
    version         integer NOT NULL DEFAULT 0,
    manifest_json   jsonb NOT NULL,
    updated_at      timestamptz NOT NULL,
    PRIMARY KEY (org_id, run_id),
    FOREIGN KEY (org_id, run_id)
        REFERENCES agent_runs (org_id, id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT runtime_workspace_overlay_manifests_version_nonnegative
        CHECK (version >= 0),
    CONSTRAINT runtime_workspace_overlay_manifests_json_object
        CHECK (jsonb_typeof(manifest_json) = 'object')
);

-- The row is worker-owned today, but the tenant policy preserves a safe
-- boundary if a future read-only API projection needs it. The worker policy
-- also grants the narrowly required parent-run lookup used to derive scope.
ALTER TABLE runtime_workspace_overlay_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_workspace_overlay_manifests FORCE ROW LEVEL SECURITY;

CREATE POLICY workspace_overlay_tenant_isolation
    ON runtime_workspace_overlay_manifests
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

CREATE POLICY workspace_overlay_worker_access
    ON runtime_workspace_overlay_manifests
    FOR ALL
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');

CREATE POLICY workspace_overlay_worker_run_lookup
    ON agent_runs
    FOR SELECT
    USING (current_setting('app.role', true) = 'worker');
