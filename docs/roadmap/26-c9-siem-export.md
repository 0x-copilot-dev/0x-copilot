# PR 26 — C9: SIEM Export Pump

**Spec ID:** C9 | **Track:** Deployment & DB | **Wave:** 7 (Operations) | **Estimated effort:** L
**Depends on:** C2
**Required for:** all bank/gov deploys

---

## 1. Functional Specification

### 1.1 Goal

Forward audit events to the customer's SIEM (Splunk HEC, Elastic, syslog/CEF) or a file (for air-gapped Compose deploys). Outbox-style cursor with exactly-once delivery semantics.

### 1.2 User-visible behavior

- **Customer security team:** sees our audit events arriving in their SIEM within seconds of write.
- **Operator:** can pause/resume the pump; can replay a date range; can inspect dead letters.
- **Air-gapped deploy:** writes JSONL to a mounted volume; customer ships it out of band.

### 1.3 Out of scope

- Webhook subscriptions for individual users.
- Custom field mapping per customer.
- Retention of forwarded events (cursor only; events themselves stay per C8).

---

## 2. Technical Specification

### 2.1 Architecture

- Single async pump in `services/backend` reads two sources:
  - `mcp_audit_events` and `identity_audit_events` directly from backend DB.
  - `runtime_audit_log` from ai-backend via internal HTTP endpoint (preserves service boundary — no cross-service DB reads).
- Per-source cursor table; advanced after successful delivery.
- Stable composite event id `{org_id}:{event_id}` for SIEM-side dedup.
- Dead letters for 4xx-class failures (don't block cursor).
- Retries with exponential backoff for 5xx.

### 2.2 Schema changes

Migration `services/backend/migrations/0013_siem_export.sql`:

```sql
CREATE TABLE siem_export_cursors (
    exporter_name      TEXT NOT NULL,
    source             TEXT NOT NULL,                  -- 'mcp_audit'|'identity_audit'|'runtime_audit_remote'
    last_event_id      TEXT,
    last_processed_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (exporter_name, source)
);

CREATE TABLE siem_export_dead_letters (
    id              TEXT PRIMARY KEY,
    exporter_name   TEXT NOT NULL,
    source          TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    last_error      TEXT NOT NULL,
    attempts        INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_siem_dead_letters_exporter ON siem_export_dead_letters (exporter_name, created_at DESC);
```

### 2.3 Endpoints

**ai-backend internal (read-only audit cursor):**

- `GET /internal/v1/audit/cursor?after_id=&limit=` — paginated, ordered `(created_at, id)` tuple cursor. Strict service-token auth + org_id pass-through. Returns `{events: [...], next_cursor}`.

**Backend admin:**

- `GET /v1/siem/exporters` — list configured exporters.
- `POST /v1/siem/exporters/{name}/pause`
- `POST /v1/siem/exporters/{name}/resume`
- `POST /v1/siem/exporters/{name}/replay?from_id=&to_id=`
- `GET /v1/siem/dead_letters?exporter=&limit=`

### 2.4 Code changes

**New** `services/backend/src/backend_app/siem_export/`:

- `interface.py` — `SiemExporter` protocol with `send(events: list[NormalizedEvent]) -> SendResult`.
- `splunk_hec.py`
- `elastic.py`
- `syslog_cef.py`
- `file.py` — JSONL writer for air-gapped.
- `null.py` — default for dev.
- `pump.py` — single async loop; runs all configured exporters in parallel coroutines.
- `normalizer.py` — converts each source's row shape to a `NormalizedEvent`.

**New** `services/ai-backend/src/runtime_api/http/audit_cursor_routes.py` — the read-only cursor endpoint with strict auth.

### 2.5 Trust model & failure semantics

- Cursor advances only on successful HTTP 2xx (or file write success).
- 4xx → dead letter, advance cursor (don't block on bad events).
- 5xx → exponential backoff, do NOT advance cursor.
- Replay endpoint resets cursor to a specific point; idempotent on SIEM side via stable id.
- Pump uses `cache_bypass=True` on auth check — revoked operator session loses access immediately.

### 2.6 Tenant isolation

- Each customer's SIEM only ever receives their org's events.
- Single-tenant deploy: pump only sees one org.
- Multi-tenant SaaS: not supported in v1 (single SIEM per deployment); per-org SIEM routing is a follow-up if a customer asks.

### 2.7 Observability

- Metrics: `siem_export_events_total{exporter,outcome}`, `siem_export_lag_seconds{exporter}`, `siem_dead_letter_count{exporter}`.
- Audit: `siem.exporter.paused/resumed`, `siem.replay.requested`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Configure Splunk HEC fake → audit events arrive within 5s.
- [ ] Restart pump mid-batch → cursor resumes; no duplicates on SIEM side (assuming idempotent ingestion via stable id).
- [ ] 5xx response from SIEM → backoff retries; cursor unchanged.
- [ ] 4xx response → event written to dead letter; cursor advances.
- [ ] File exporter: JSONL appended with valid records.
- [ ] Replay endpoint resets cursor and re-sends in order.

### 3.2 Test plan

**Per exporter:**

- Success path.
- 5xx retry path.
- 4xx dead letter path.

**Cross-source:**

- Fetch ai-backend cursor returns events in order; pump processes both backend-local and remote sources.

**Idempotency:**

- Same event id sent twice → SIEM receives twice (with same composite id) but customer-side dedup is on them — verify via integration test snapshotting outbound HTTP body.

**Resumption:**

- Kill pump mid-batch → restart → cursor at exact resume point.

**Tenant-isolation:**

- Two orgs: only the configured org's events reach the SIEM.

### 3.3 Compliance evidence produced

- End-to-end audit-event export to customer SIEM, demonstrated by CI test.
- Cursor table demonstrates auditability of "what was exported, when, by which exporter."
- Dead-letter table queryable; runbook for replay.

### 3.4 Rollout plan

Default backend `null` → no behavior change. Per-deployment opt-in.

### 3.5 Backout plan

Set backend to `null`. Cursor stays where it was.

### 3.6 Definition of done

- [ ] Migration 0013 applied.
- [ ] All four exporters implemented + tested.
- [ ] Admin endpoints live.
- [ ] `docs/security/siem-export.md` runbook written.
- [ ] Air-gapped Compose deploy documented with file exporter.

---

## 4. Critical files

- New: `services/backend/migrations/0013_siem_export.sql` (+ rollback)
- New: `services/backend/src/backend_app/siem_export/` (multiple files)
- New: `services/ai-backend/src/runtime_api/http/audit_cursor_routes.py`
- New: `services/backend/src/backend_app/admin/siem_routes.py`
- New: `docs/security/siem-export.md`
