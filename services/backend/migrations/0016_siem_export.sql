-- C9: SIEM export pump.
--
-- The pump runs in the backend service and forwards audit events to the
-- customer's SIEM (Splunk HEC, Elastic, syslog/CEF, file). Two tables:
--
--   siem_export_cursors      — one row per (exporter_name, source). Advances
--                              only on successful 2xx delivery so a process
--                              kill can't lose events.
--   siem_export_dead_letters — events that produced a 4xx-class failure
--                              (the cursor advances past them so they don't
--                              block the pump). Operators replay via the
--                              admin endpoints.
--
-- ``source`` is one of:
--   - ``mcp_audit``           — services/backend mcp_audit_events
--   - ``identity_audit``      — services/backend identity_audit_events
--   - ``runtime_audit_remote`` — services/ai-backend runtime_audit_log via
--                                an internal HTTP cursor endpoint (preserves
--                                the service boundary; no cross-service DB
--                                reads).

CREATE TABLE IF NOT EXISTS siem_export_cursors (
    exporter_name      TEXT NOT NULL,
    source             TEXT NOT NULL,
    last_event_id      TEXT,
    last_processed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (exporter_name, source)
);

CREATE TABLE IF NOT EXISTS siem_export_dead_letters (
    id              TEXT PRIMARY KEY,
    exporter_name   TEXT NOT NULL,
    source          TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    last_error      TEXT NOT NULL,
    attempts        INTEGER NOT NULL CHECK (attempts >= 1),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_siem_dead_letters_exporter
    ON siem_export_dead_letters (exporter_name, created_at DESC);
