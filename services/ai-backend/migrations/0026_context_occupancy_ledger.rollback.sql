-- Rollback 0026_context_occupancy_ledger: drop the occupancy relation.
--
-- Purely additive forward migration, so the rollback is a single DROP. It
-- deliberately does NOT touch `agent_runs_org_id_id_key`: that unique
-- constraint is owned by 0014 and is still referenced by
-- `runtime_workspace_overlay_manifests`.

DROP TABLE IF EXISTS runtime_context_occupancy;
