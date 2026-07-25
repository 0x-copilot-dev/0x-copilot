-- Rollback 0012_repair_planning_snapshots.

DROP INDEX IF EXISTS idx_runtime_effect_claims_repair_scan;
DROP TABLE IF EXISTS runtime_repair_planning_scan_state;
DROP TABLE IF EXISTS runtime_repair_planning_outcomes;
DROP TABLE IF EXISTS runtime_repair_planning_snapshots;
