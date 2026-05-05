-- C9: per-exporter pause/resume + replay control state.
--
-- The pump consults this table at the start of each tick to skip paused
-- exporters and to honor any pending replay window. Operators set the
-- state via the /v1/siem/exporters/* admin endpoints; the row survives
-- restarts so a kill mid-pause doesn't accidentally resume export.

CREATE TABLE IF NOT EXISTS siem_exporter_controls (
    exporter_name      TEXT PRIMARY KEY,
    paused_at          TIMESTAMPTZ,
    replay_from_id     TEXT,
    replay_to_id       TEXT,
    replay_requested_at TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_user_id TEXT
);
