DROP TRIGGER IF EXISTS runtime_legal_holds_artifact_pin_after ON runtime_legal_holds;
DROP TRIGGER IF EXISTS runtime_legal_holds_artifact_fence_before ON runtime_legal_holds;
DROP FUNCTION IF EXISTS runtime_artifact_hold_pin_or_release();
DROP FUNCTION IF EXISTS runtime_artifact_hold_acquire_fences();
