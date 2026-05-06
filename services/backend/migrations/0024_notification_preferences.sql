-- PR B4: typed notification preferences + quiet hours.
--
-- Replaces the JSONB `user_preferences.preferences.notifications`
-- blob (PR 4.1 placeholder) with two indexed tables:
--
--   notification_preferences: one row per (user, event_kind, channel).
--     Cell value = `enabled BOOLEAN`. New event kinds and channels
--     are forward-additive: any (user, event, channel) triple absent
--     from the table inherits the deployment default.
--
--   notification_quiet_hours: one row per user. `enabled` toggles the
--     entire feature. `from_local`/`to_local` are TIME (no timezone
--     bound to the row); `tz` is an IANA tz id read at dispatch time.
--     During quiet hours, only `approval_requested` (which is
--     critical-by-default) breaks through.
--
-- The dispatcher (out of scope for this PR) reads from these tables
-- on every send; absence of a row means "use deployment default."

CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id      TEXT         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_kind   TEXT         NOT NULL CHECK (event_kind IN (
                              'long_task_finished',
                              'approval_requested',
                              'mention',
                              'connector_error',
                              'weekly_digest',
                              'product_updates'
                              )),
    channel      TEXT         NOT NULL CHECK (channel IN ('in_app', 'email', 'push')),
    enabled      BOOLEAN      NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, event_kind, channel)
);

CREATE TABLE IF NOT EXISTS notification_quiet_hours (
    user_id      TEXT         PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    enabled      BOOLEAN      NOT NULL DEFAULT FALSE,
    from_local   TIME         NOT NULL DEFAULT '20:00',
    to_local     TIME         NOT NULL DEFAULT '08:00',
    tz           TEXT         NOT NULL DEFAULT 'UTC',
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
