# Environment Variables Reference

All environment variables consumed by `ai-backend`. Resolved in `agent_runtime/settings.py`
via `RuntimeSettings` (Pydantic `BaseSettings`).

Variables marked **required in production** will cause startup to fail if unset when
`BACKEND_ENVIRONMENT != development`.

---

## Core runtime

| Variable                           | Default       | Description                                                                                                       |
| ---------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------- |
| `BACKEND_ENVIRONMENT`              | `development` | `development` or `production`. Enables dev-only routes when `development`.                                        |
| `RUNTIME_STORE_BACKEND`            | `in_memory`   | `in_memory`, `in_memory_async`, or `postgres`. See [architecture/03-adapters.md](../architecture/03-adapters.md). |
| `RUNTIME_START_IN_PROCESS_WORKER`  | `false`       | Start a worker coroutine inside the API process (useful for local dev without a separate worker process).         |
| `DATABASE_URL`                     | —             | Postgres connection URL. Required when `RUNTIME_STORE_BACKEND=postgres`.                                          |
| `RUNTIME_AUTO_MIGRATE`             | `true`        | Run DB migrations at startup.                                                                                     |
| `RUNTIME_WORKER_CONCURRENCY`       | `4`           | Number of concurrent claim loops in the worker process.                                                           |
| `RUNTIME_WORKER_HEARTBEAT_SECONDS` | `30`          | How often the worker extends its claim lock.                                                                      |
| `RUNTIME_CLAIM_LOCK_TTL_SECONDS`   | `300`         | How long a claim lock is held before it expires (allows crashed worker claims to be reclaimed).                   |

---

## Auth and security

| Variable                     | Default     | Description                                                                                                                                            |
| ---------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ENTERPRISE_AUTH_SECRET`     | —           | **Required in prod.** Secret for signing and verifying bearer tokens.                                                                                  |
| `ENTERPRISE_SERVICE_TOKEN`   | —           | **Required in prod.** Token for internal service-to-service calls. Callers must also provide `x-enterprise-org-id` and `x-enterprise-user-id` headers. |
| `RUNTIME_ENCRYPTION_BACKEND` | `local_dev` | `local_dev` (no-op) or `aws_kms`.                                                                                                                      |
| `RUNTIME_KMS_KEY_ARN`        | —           | Required when `RUNTIME_ENCRYPTION_BACKEND=aws_kms`.                                                                                                    |

---

## Model providers

| Variable            | Default | Description                                                           |
| ------------------- | ------- | --------------------------------------------------------------------- |
| `OPENAI_API_KEY`    | —       | OpenAI key (stored in `.env` for local dev; never in request bodies). |
| `ANTHROPIC_API_KEY` | —       | Anthropic key.                                                        |
| `GOOGLE_API_KEY`    | —       | Google / Gemini key.                                                  |

---

## Backend integration (internal API)

| Variable                           | Default                 | Description                                      |
| ---------------------------------- | ----------------------- | ------------------------------------------------ |
| `BACKEND_INTERNAL_BASE_URL`        | `http://localhost:8100` | Base URL for `backend`'s `/internal/v1/` routes. |
| `BACKEND_INTERNAL_TIMEOUT_SECONDS` | `10`                    | HTTP timeout for internal backend calls.         |

---

## SSE and event bus

| Variable                                 | Default          | Description                                                                         |
| ---------------------------------------- | ---------------- | ----------------------------------------------------------------------------------- |
| `RUNTIME_SSE_FALLBACK_POLL_SECONDS`      | `2.0`            | How long `RuntimeSseAdapter` waits on the event bus before polling the event store. |
| `RUNTIME_EVENT_BUS_BACKEND`              | auto             | `in_memory` or `postgres`. Auto-selected based on `RUNTIME_STORE_BACKEND`.          |
| `RUNTIME_POSTGRES_NOTIFY_CHANNEL_PREFIX` | `runtime_events` | Prefix for Postgres LISTEN/NOTIFY channel names.                                    |

---

## Budgets and pricing

| Variable                                   | Default | Description                                                      |
| ------------------------------------------ | ------- | ---------------------------------------------------------------- |
| `RUNTIME_PRICING_REFRESH_INTERVAL_SECONDS` | `3600`  | How often `ModelPricingCatalog` is refreshed from LiteLLM + DB.  |
| `RUNTIME_DEFAULT_TOOL_BUDGET_PER_RUN`      | `5`     | Default per-run tool invocation cap (overridable per workspace). |

---

## Usage rollup

| Variable                                | Default | Description                                                            |
| --------------------------------------- | ------- | ---------------------------------------------------------------------- |
| `RUNTIME_USAGE_ROLLUP_INTERVAL_SECONDS` | `300`   | How often the rollup loop aggregates per-call rows into daily buckets. |

---

## Retention

| Variable                                   | Default | Description                                              |
| ------------------------------------------ | ------- | -------------------------------------------------------- |
| `RUNTIME_RETENTION_DEFAULT_DAYS`           | `90`    | Default conversation retention period in days.           |
| `RUNTIME_RETENTION_AUDIT_DAYS`             | `365`   | Default retention for `AUDIT` visibility events.         |
| `RUNTIME_RETENTION_SWEEP_INTERVAL_SECONDS` | `3600`  | How often the retention sweeper runs.                    |
| `RUNTIME_RETENTION_SWEEP_BATCH_SIZE`       | `500`   | Max rows deleted per sweep pass per kind.                |
| `RUNTIME_ENABLE_RETENTION_BACKFILL`        | `false` | Enable the one-time `retention_backfill` job at startup. |

---

## Observability

| Variable                      | Default      | Description                                                                               |
| ----------------------------- | ------------ | ----------------------------------------------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | —            | OTLP endpoint for OTEL trace export. If unset, tracing is no-op.                          |
| `OTEL_SERVICE_NAME`           | `ai-backend` | Service name in OTEL spans.                                                               |
| `RUNTIME_LOG_LEVEL`           | `INFO`       | Python log level.                                                                         |
| `RUNTIME_HTTP_LOG_LEVEL`      | `WARNING`    | Log level for HTTP request/response logs.                                                 |
| `RUNTIME_REDACT_PAYLOADS`     | `true`       | Whether `ObservabilityRedactor` strips sensitive keys from event payloads before logging. |

---

## Local dev only

| Variable                            | Default | Description                                                        |
| ----------------------------------- | ------- | ------------------------------------------------------------------ |
| `RUNTIME_DEV_SKIP_BUDGET_PREFLIGHT` | `false` | Skip budget enforcement in local dev (avoids seeding budget rows). |
| `RUNTIME_DEV_PRICING_STUB`          | `false` | Use zero-cost pricing stub instead of real catalog.                |
