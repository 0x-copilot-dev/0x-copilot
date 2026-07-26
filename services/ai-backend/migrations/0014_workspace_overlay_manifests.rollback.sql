-- Rollback 0014_workspace_overlay_manifests.

DROP POLICY IF EXISTS workspace_overlay_worker_run_lookup ON agent_runs;
DROP TABLE IF EXISTS runtime_workspace_overlay_manifests;
ALTER TABLE agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_org_id_id_key;
