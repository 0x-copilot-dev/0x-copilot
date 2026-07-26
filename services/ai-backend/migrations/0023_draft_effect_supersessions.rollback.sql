-- Rollback 0023_draft_effect_supersessions.
-- Never discard a safety correlation after it has been written: operators must
-- first drain/reconcile the affected stages before rolling back this contract.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM runtime_draft_effect_supersessions) THEN
        RAISE EXCEPTION 'cannot roll back 0023 with persisted draft effect supersessions';
    END IF;
END $$;

DROP TABLE IF EXISTS runtime_draft_effect_supersessions;
