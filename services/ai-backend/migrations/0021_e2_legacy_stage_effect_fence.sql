-- 0021_e2_legacy_stage_effect_fence — P1 source reservation + queue cancel.
--
-- A reservation is the durable compare-and-swap boundary between advisory
-- legacy inventory and creation of a canonical held effect stage.  It stores
-- identifiers/digests only: no proposal body, command payload, approval, or
-- executor data is persisted here.

CREATE TABLE IF NOT EXISTS runtime_e2_legacy_stage_reservations (
    org_id              text NOT NULL,
    run_id              text NOT NULL,
    legacy_stage_id     text NOT NULL,
    source_digest       text NOT NULL,
    idempotency_key     text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, run_id, legacy_stage_id),
    CONSTRAINT runtime_e2_legacy_stage_reservation_source_digest_check
        CHECK (source_digest ~ '^[0-9a-f]{64}$')
);

ALTER TABLE runtime_e2_legacy_stage_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_e2_legacy_stage_reservations FORCE ROW LEVEL SECURITY;

CREATE POLICY e2_legacy_stage_reservation_worker_only
    ON runtime_e2_legacy_stage_reservations
    USING (current_setting('app.role', true) = 'worker')
    WITH CHECK (current_setting('app.role', true) = 'worker');

GRANT SELECT, INSERT ON runtime_e2_legacy_stage_reservations TO enterprise_app;

-- ``cancelled`` is terminal and deliberately excluded from worker claims.  It
-- names operator-neutralized old work truthfully instead of overloading a
-- dead-letter failure and accidentally making it eligible for retry tooling.
ALTER TABLE runtime_outbox_events
    DROP CONSTRAINT IF EXISTS runtime_outbox_events_status_check;

ALTER TABLE runtime_outbox_events
    ADD CONSTRAINT runtime_outbox_events_status_check
    CHECK (status = ANY (ARRAY[
        'pending'::text,
        'claimed'::text,
        'completed'::text,
        'retry'::text,
        'dead_letter'::text,
        'cancelled'::text
    ]));
