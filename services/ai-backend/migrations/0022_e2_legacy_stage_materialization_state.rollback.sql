-- Rollback 0022_e2_legacy_stage_materialization_state.
-- Refuse to erase a state machine once it has materialized any canonical work.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM runtime_e2_legacy_stage_reservations
         WHERE materialization_state IN ('staged', 'mapped')
    ) THEN
        RAISE EXCEPTION 'cannot roll back 0022 after legacy materialization';
    END IF;
END $$;

DROP TABLE IF EXISTS runtime_e2_legacy_stage_evidence;
DROP TABLE IF EXISTS runtime_e2_legacy_stage_reconciliations;

ALTER TABLE runtime_e2_legacy_stage_reservations
    DROP CONSTRAINT IF EXISTS runtime_e2_legacy_stage_reservation_state_check,
    DROP CONSTRAINT IF EXISTS runtime_e2_legacy_stage_reservation_revision_check,
    DROP COLUMN IF EXISTS updated_at,
    DROP COLUMN IF EXISTS revision,
    DROP COLUMN IF EXISTS materialization_state,
    DROP COLUMN IF EXISTS canonical_stage_id;
