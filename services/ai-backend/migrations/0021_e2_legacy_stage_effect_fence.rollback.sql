-- Rollback 0021_e2_legacy_stage_effect_fence.
-- Refuse a rollback that would reinterpret a deliberately cancelled legacy
-- command as claimable work under a mixed-version deployment.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM runtime_outbox_events WHERE status = 'cancelled') THEN
        RAISE EXCEPTION 'cannot roll back 0021 while cancelled outbox commands exist';
    END IF;
END $$;

ALTER TABLE runtime_outbox_events
    DROP CONSTRAINT IF EXISTS runtime_outbox_events_status_check;

ALTER TABLE runtime_outbox_events
    ADD CONSTRAINT runtime_outbox_events_status_check
    CHECK (status = ANY (ARRAY[
        'pending'::text,
        'claimed'::text,
        'completed'::text,
        'retry'::text,
        'dead_letter'::text
    ]));

DROP TABLE IF EXISTS runtime_e2_legacy_stage_reservations;
