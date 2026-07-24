DROP POLICY IF EXISTS artifact_evidence_merge_worker_update
    ON runtime_deletion_evidence;
DROP POLICY IF EXISTS artifact_evidence_merge_worker_select
    ON runtime_deletion_evidence;
DROP TABLE IF EXISTS runtime_artifact_gc_quarantine CASCADE;
DROP TABLE IF EXISTS runtime_artifact_gc_candidates CASCADE;
DROP TABLE IF EXISTS runtime_artifact_reference_edges CASCADE;
DROP TABLE IF EXISTS runtime_artifact_idempotency CASCADE;
DROP TABLE IF EXISTS runtime_artifact_revisions CASCADE;
DROP TABLE IF EXISTS runtime_artifacts CASCADE;
