# SIEM Export Pump (C9)

This runbook covers the audit-event export pump that ships rows from
`mcp_audit_events`, `identity_audit_events`, and ai-backend's
`runtime_audit_log` to a customer-side SIEM.

## Architecture

A single async loop in the backend service runs every
`SIEM_PUMP_INTERVAL_SECONDS` (default 5). For each (exporter, source)
pair it:

1. Reads the cursor from `siem_export_cursors`.
2. Fetches the next batch (after_id, ordered by `(created_at, id)`).
3. Normalizes each row to a `NormalizedEvent` (composite id, source
   discriminator, severity, payload).
4. Hands the batch to the exporter's `send(...)`.
5. Classifies the response: 2xx → advance cursor; 4xx → write to
   `siem_export_dead_letters` and advance; 5xx / transport →
   leave cursor, exponential per-source backoff.

ai-backend's `runtime_audit_log` is read over an internal HTTP cursor
(`GET /internal/v1/audit/cursor`) so the pump never touches the
ai-backend DB directly — that preserves the monorepo's hard service
boundary.

## Schema

Migration `0016_siem_export.sql` adds two tables:

- `siem_export_cursors` — `(exporter_name, source) → last_event_id`.
  Advances only on success; persists across pump restarts.
- `siem_export_dead_letters` — events that produced a 4xx-class
  rejection. Operators inspect, fix the upstream config, and replay.

## Exporters

| Backend      | When to use                                                              |
| ------------ | ------------------------------------------------------------------------ |
| `null`       | Default. Drops events; cursor still advances.                            |
| `file`       | Air-gapped Compose deploys. Writes JSONL to `SIEM_EXPORTER_FILE_PATH`.   |
| `splunk_hec` | Splunk HTTP Event Collector. JSON over HTTPS with `Splunk <token>` auth. |
| `elastic`    | Elastic `_bulk` API. Composite id pinned as `_id` for de-dup.            |
| `syslog_cef` | RFC 5424 syslog frame with ArcSight CEF body, UDP or TCP.                |

Pick a backend with `SIEM_EXPORTER_BACKEND`. Exporter-specific env vars:

```bash
# Splunk HEC
SIEM_EXPORTER_BACKEND=splunk_hec
SIEM_EXPORTER_SPLUNK_URL=https://splunk-hec.company.local:8088
SIEM_EXPORTER_SPLUNK_TOKEN=...

# Elastic
SIEM_EXPORTER_BACKEND=elastic
SIEM_EXPORTER_ELASTIC_URL=https://es.company.local:9200
SIEM_EXPORTER_ELASTIC_INDEX=audit-events
SIEM_EXPORTER_ELASTIC_API_KEY=...

# Syslog/CEF
SIEM_EXPORTER_BACKEND=syslog_cef
SIEM_EXPORTER_SYSLOG_HOST=siem.company.local
SIEM_EXPORTER_SYSLOG_PORT=514
SIEM_EXPORTER_SYSLOG_PROTOCOL=udp     # or tcp

# File (air-gapped)
SIEM_EXPORTER_BACKEND=file
SIEM_EXPORTER_FILE_PATH=/var/log/siem/audit.jsonl
```

## Configuration

Pump-wide env vars:

```bash
SIEM_PUMP_ENABLED=true                   # default false; opt-in
SIEM_PUMP_INTERVAL_SECONDS=5             # default 5
SIEM_PUMP_BATCH_SIZE=100                 # default 100
AI_BACKEND_INTERNAL_BASE_URL=http://ai-backend:8000
ENTERPRISE_SERVICE_TOKEN=...             # required to fetch the runtime audit cursor
```

## De-duplication contract

Every event carries a stable composite id `{org_id}:{event_id}`. SIEMs
that ingest with this as a key (Splunk dedup, Elastic `_id`, etc.) will
treat retries as idempotent. Customer-side dedup is on the customer —
the pump does not guarantee at-most-once because retry on 5xx is the
right safety choice for compliance evidence.

## Replay

Operator replays a date range by:

1. `UPDATE siem_export_cursors SET last_event_id = NULL` (full reset)
   or to a specific prior id.
2. Pump tick will re-emit everything from that point forward; SIEM
   handles the dup via composite id.

A `POST /v1/siem/exporters/{name}/replay?from_id=&to_id=` admin
endpoint is documented in the C9 spec but ships in a follow-up
alongside A10 RBAC.

## Air-gapped Compose deploys

Use `file` backend writing to a host-mounted volume; customer ships the
JSONL out-of-band on their schedule. One JSON object per line so
`jq`/`logstash` can ingest with no parsing.

## Observability

The pump emits standard backend logs (`siem_pump_tick_failed`,
`siem_pump_source_failed`). OTel metrics
(`siem_export_events_total{exporter,outcome}`,
`siem_export_lag_seconds`, `siem_dead_letter_count`) ship in C11
alongside the rest of the Wave 7 observability work.

## Backout

```bash
SIEM_EXPORTER_BACKEND=null
```

Cursors stay where they are; restarting the pump with a real backend
resumes from the same point.
