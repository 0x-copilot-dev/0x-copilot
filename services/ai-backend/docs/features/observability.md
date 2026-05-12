# Observability

How the service handles structured logging, OTEL tracing, payload redaction,
and the immutable audit chain.

See also:

- [architecture/04-security-invariants.md](../architecture/04-security-invariants.md) — redaction boundary
- [features/usage-metrics.md](usage-metrics.md) — usage recording (separate from observability)

---

## What it does

Three distinct concerns:

1. **Structured logging + redaction** — every log line is JSON; sensitive fields are stripped by
   `ObservabilityRedactor` before emission. Persistence and SSE carry data whole — only logs are redacted.
2. **OTEL tracing** — every run carries a `trace_id`; spans flow through the worker and into provider
   API calls.
3. **Audit chain** — append-only hash-chained audit rows for compliance. Separate retention period
   from regular events.

---

## Key modules

| File                                              | Role                                                                |
| ------------------------------------------------- | ------------------------------------------------------------------- |
| `agent_runtime/observability/redactor.py`         | `ObservabilityRedactor` — strips sensitive fields from log payloads |
| `agent_runtime/observability/tracing.py`          | `RuntimeTracer` — OTEL span lifecycle for runs                      |
| `agent_runtime/observability/otel.py`             | OTEL SDK setup, exporter config                                     |
| `agent_runtime/observability/logging.py`          | Structured JSON log formatter                                       |
| `agent_runtime/observability/http_logging.py`     | HTTP request/response log middleware                                |
| `agent_runtime/observability/lifecycle_ledger.py` | `LifecycleLedger` — tracks run open/close for orphan detection      |
| `agent_runtime/observability/attribution.py`      | `UsageAttributionContext` — ties usage to connector/purpose         |
| `agent_runtime/observability/usage_recorder.py`   | `PostgresUsageRecorder` — persists per-call token rows              |

---

## Redaction — structural approach

`agent_runtime/observability/redactor.py`

The redactor does **not** scan values for PII patterns. It uses **structural redaction**:

- `Sensitive[]` Pydantic field annotations mark specific model fields as sensitive. The log
  serializer elides these before emission.
- An exact-match deny-key set covers unstructured `dict` metadata (e.g. known-sensitive key
  names like `"token"`, `"secret"`, `"password"`, `"api_key"`). No regex.

**Why not value scanning?** Regex-based PII detection produces false positives on legitimate data
(e.g. UK postcodes matching credit card patterns), cannot be reliably unit-tested, and has high
maintenance cost. Structural redaction is deterministic and testable: if a field is `Sensitive[]`,
it is always elided.

**Redaction boundary:** Logs are the only redaction surface. `EventStorePort` and SSE carry data
whole to authorised consumers. This is the correct compliance boundary — logs feed SIEM and are
accessible to operators; the event store and SSE feed the authenticated frontend.

**User-content carve-out:** User message text in logs is length-clipped (default 256 chars) but not
value-redacted, since message content cannot be pre-classified as sensitive.

---

## OTEL tracing

`agent_runtime/observability/tracing.py`

Every run gets a `trace_id` assigned at `create_run` time (or passed in from the facade if the
request carried an existing trace context). The trace flows through:

- `RuntimeRunHandler` — opens a root span for the worker run
- `acreate_agent_runtime()` — opens child spans for factory/load phases
- Provider stream adapters — propagate trace headers to LLM provider API calls
- `McpClient.call_tool()` — propagates trace headers to backend → MCP server calls

Configure export via `OTEL_EXPORTER_OTLP_ENDPOINT`. If unset, all spans are no-op (zero overhead).

---

## Audit chain

`agent_runtime/observability/attribution.py`, `lifecycle_ledger.py`

The audit event table is append-only with a hash chain:

- Each row stores `HMAC-SHA256(prev_hash || payload, key_version)`.
- Per-org chain isolation: each org's rows are chained independently.
- Three-layer immutability enforced by Postgres:
  1. `audit_writer` role has `INSERT` only (no `UPDATE`/`DELETE`).
  2. A trigger raises on any `UPDATE` or `DELETE` attempt.
  3. The hash chain detects tampering in the write→export window (SIEM cursor alone cannot detect this).

Key rotation: `key_version` on each row allows historical verification after key rotation.

**Audit events vs. runtime events:** Audit events use `RuntimeEventVisibility.AUDIT` and have a
longer retention period (`RUNTIME_RETENTION_AUDIT_DAYS`, default 365 days). They are accessible via
`GET /v1/admin/audit/events` (admin scope only) and exportable to customer SIEM.

---

## `UsageAttributionContext`

`agent_runtime/observability/attribution.py`

Every model call is attributed to a `Purpose` and an optional `connector_slug`:

| `Purpose`       | When                                                        |
| --------------- | ----------------------------------------------------------- |
| `MAIN`          | Main user-initiated model call                              |
| `SUBAGENT_WORK` | Model call inside a subagent run (`subagent_slug` required) |
| `SUMMARIZE`     | Context compression LLM call                                |
| `SYSTEM`        | Internal system call (rare)                                 |

The attribution context is **carried** through the call chain (not reconstructed after the fact)
to avoid attribution loss during compression calls and parallel subagent calls.

Validation rule: `Purpose.SUBAGENT_WORK` requires `subagent_slug != None` — enforced by Pydantic
field validator on `UsageAttributionContext`.

---

## Lifecycle ledger

`agent_runtime/observability/lifecycle_ledger.py`

`LifecycleLedger` tracks which runs are currently open in the worker process. On worker shutdown
(graceful or crash), any open run is detectable and can be transitioned to `FAILED` or `AWAITING_RETRY`
by the recovery process. This prevents "zombie" runs sitting in `RUNNING` status indefinitely.

---

## Structured log fields

Every log record emitted by this service includes:

- `trace_id` — from the current OTEL span context
- `run_id` — if logging within a run's scope
- `org_id` — from the current request context
- `service` — always `ai-backend`
- `level` — `DEBUG`, `INFO`, `WARNING`, `ERROR`

Sensitive fields (Pydantic `Sensitive[]` annotations) are replaced with `[REDACTED]` in the
JSON output.
