# Environment Variables Reference

All environment variables consumed by `ai-backend`. Resolved in `agent_runtime/settings.py`
via `RuntimeSettings` (Pydantic `BaseSettings`).

Variables marked **required in production** will cause startup to fail if unset when
`BACKEND_ENVIRONMENT != development`.

---

## Core runtime

| Variable                             | Default       | Description                                                                                                                                                                                                               |
| ------------------------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RUNTIME_ENVIRONMENT`                | `development` | `development`, `test`, or `production`. Enables dev-only runtime composition only outside production.                                                                                                                     |
| `RUNTIME_STORE_BACKEND`              | `in_memory`   | `in_memory`, `in_memory_async`, `postgres`, or `file`. `file` is `single_user_desktop`-only and needs `RUNTIME_FILE_STORE_ROOT`. See [architecture/03-adapters.md](../architecture/03-adapters.md).                       |
| `RUNTIME_START_IN_PROCESS_WORKER`    | `false`       | Start a worker coroutine inside the API process. Honored for single-process deployments only — in-memory dev/test and the `single_user_desktop` profile (any backend); server profiles run a dedicated worker regardless. |
| `RUNTIME_FILE_STORE_ROOT`            | —             | Absolute root for the file-native store. Required when `RUNTIME_STORE_BACKEND=file`.                                                                                                                                      |
| `RUNTIME_FILE_STORE_MAX_BYTES`       | `0`           | Whole-store byte ceiling for the desktop file store. `0` means unlimited; packaged desktop deployments should set a finite value. Evaluation shares this quota and CAS.                                                   |
| `RUNTIME_FILE_STORE_RETENTION_DAYS`  | `0`           | Age-based desktop file-store cleanup window. `0` keeps history until explicit deletion.                                                                                                                                   |
| `RUNTIME_FILE_STORE_COMPACTION`      | `true`        | Boot-time bounded-growth compaction of the file store's append-with-fold state ledgers. `0`/`false`/`off` disables it (kill switch).                                                                                      |
| `RUNTIME_EVALUATION_STORE_ROOT`      | —             | Explicit absolute shared root for the evaluation ledger/CAS when the primary store is Postgres. Required for hosted projection and any release state that needs an evaluation repository.                                 |
| `RUNTIME_EVALUATION_STORE_MAX_BYTES` | `536870912`   | Byte ceiling for the dedicated evaluation CAS beside Postgres. Desktop file-store composition ignores this value and shares `RUNTIME_FILE_STORE_MAX_BYTES`.                                                               |
| `DATABASE_URL`                       | —             | Postgres connection URL. Required when `RUNTIME_STORE_BACKEND=postgres`.                                                                                                                                                  |
| `RUNTIME_AUTO_MIGRATE`               | `true`        | Run DB migrations at startup.                                                                                                                                                                                             |
| `RUNTIME_WORKER_CONCURRENCY`         | `4`           | Number of concurrent claim loops in the worker process.                                                                                                                                                                   |
| `RUNTIME_WORKER_HEARTBEAT_SECONDS`   | `30`          | How often the worker extends its claim lock.                                                                                                                                                                              |
| `RUNTIME_CLAIM_LOCK_TTL_SECONDS`     | `300`         | How long a claim lock is held before it expires (allows crashed worker claims to be reclaimed).                                                                                                                           |

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

## Harness evaluation and release control

All evaluation settings resolve once at process startup. See
[Harness evaluation and release operations](../runbooks/harness-evaluation-release-operations.md)
for consent, release schema, diagnostics, restart, rollback, and deletion
procedures. F10 uses the same signed release path; see
[Model invocation reliability operations](../runbooks/model-invocation-reliability-operations.md)
for mode, recovery-control, incident, and backout guidance. There are no
standalone `F10_*` environment variables: independent retry, alternate,
equivalent, and circuit controls must come from reviewed composition.

| Variable                                      | Default                           | Bounds / description                                                                                                                                                                                                                                |
| --------------------------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RUNTIME_EVALUATION_PROJECTION_ENABLED`       | `false`                           | Master scheduling/claim gate. Projection is still dark unless user consent also resolves true.                                                                                                                                                      |
| `RUNTIME_EVALUATION_USER_CONSENTED`           | `false`                           | Explicit local user-consent gate. An organization role or release cannot imply consent.                                                                                                                                                             |
| `RUNTIME_EVALUATION_ALLOW_DEVELOPMENT_RUNS`   | `false`                           | Additional opt-in required to project runs when `RUNTIME_ENVIRONMENT` is `development` or `test`.                                                                                                                                                   |
| `RUNTIME_EVALUATION_PROFILE_ID`               | `desktop-local-profile`           | Local evaluation namespace, 1–160 characters. It is not sourced from request identity.                                                                                                                                                              |
| `RUNTIME_EVALUATION_PROJECT_ID`               | —                                 | Optional project sub-scope, 1–160 characters when set.                                                                                                                                                                                              |
| `RUNTIME_EVALUATION_POLICY_REVISION`          | `evaluation-projection-policy-v1` | Immutable projection-policy revision persisted with each job.                                                                                                                                                                                       |
| `RUNTIME_EVALUATION_REDACTION_REVISION`       | `evaluation-redaction-v1`         | Immutable redaction-policy revision persisted with each trajectory.                                                                                                                                                                                 |
| `RUNTIME_EVALUATION_MAX_EVENTS_PER_RUN`       | `10000`                           | Maximum terminal sequence/events read for one projection; `1..100000`.                                                                                                                                                                              |
| `RUNTIME_EVALUATION_MAX_PROJECTION_ATTEMPTS`  | `3`                               | Maximum durable claim attempts after crash/lease recovery; `1..10`.                                                                                                                                                                                 |
| `RUNTIME_EVALUATION_PROJECTION_LEASE_SECONDS` | `60`                              | Projection claim lease; `1..3600` seconds.                                                                                                                                                                                                          |
| `RUNTIME_EVALUATION_PROJECTION_CLAIM_BATCH`   | `20`                              | Maximum candidate jobs enumerated per pass; `1..100`. The runner executes at most one job per pass.                                                                                                                                                 |
| `RUNTIME_HARNESS_RELEASE_CONFIG_PATH`         | —                                 | Absolute path to a regular, non-symlink deployment JSON file, at most 1 MiB, containing public verification keys and the complete assignment catalog. An invalid active release fails startup.                                                      |
| `RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED`       | `false`                           | Mount authenticated loopback verify/install/rollback/export routes. Requires a release config and non-empty `ENTERPRISE_SERVICE_TOKEN`, and is rejected in production. Install/rollback needs a controlled API/worker restart to apply to new runs. |

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

## Local models (Ollama)

| Variable                              | Default | Description                                                                                                                                                                                                                                        |
| ------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RUNTIME_ENABLE_LOCAL_MODELS`         | `false` | Expose the `/v1/local-models/*` management API. `/status` always answers; every other route 404s while this is false. Set by the desktop runtime and self-host.                                                                                    |
| `RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME` | `false` | Allow this server to detect the host's `ollama` binary and spawn `ollama serve` (PRD-P8 D2). Gates `POST /v1/local-models/runtime/start` (404 while false) and `LocalModelsStatus.runtime_state`, which stays `unknown` while false. Desktop-only. |

`runtime_state` derivation (server-side, single source of truth):

| Condition                     | `runtime_state` |
| ----------------------------- | --------------- |
| feature disabled              | `unknown`       |
| daemon answers `/api/version` | `running`       |
| `MANAGE_RUNTIME` off          | `unknown`       |
| binary found on this machine  | `stopped`       |
| otherwise                     | `not_installed` |

---

## Local dev only

| Variable                            | Default | Description                                                        |
| ----------------------------------- | ------- | ------------------------------------------------------------------ |
| `RUNTIME_DEV_SKIP_BUDGET_PREFLIGHT` | `false` | Skip budget enforcement in local dev (avoids seeding budget rows). |
| `RUNTIME_DEV_PRICING_STUB`          | `false` | Use zero-cost pricing stub instead of real catalog.                |
