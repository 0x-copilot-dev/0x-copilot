# Environment Variables — backend-facade

All env vars read by `services/backend-facade`.

---

## Core / auth

| Variable                   | Default       | Notes                                                                                     |
| -------------------------- | ------------- | ----------------------------------------------------------------------------------------- |
| `ENTERPRISE_AUTH_SECRET`   | —             | **Required.** HMAC key for verifying session bearer tokens; must match `backend`'s value  |
| `ENTERPRISE_SERVICE_TOKEN` | —             | **Required in prod.** Injected into all upstream requests as `X-Enterprise-Service-Token` |
| `FACADE_ENVIRONMENT`       | `development` | `development` enables dev IdP proxy; `production` enforces safety checks                  |
| `REQUIRE_SESSION_BINDING`  | `false`       | When `true`, bearers without `sid` claim are rejected (A2 hardening)                      |

---

## Upstream URLs

| Variable         | Default                 | Notes                          |
| ---------------- | ----------------------- | ------------------------------ |
| `BACKEND_URL`    | `http://127.0.0.1:8100` | `services/backend` base URL    |
| `AI_BACKEND_URL` | `http://127.0.0.1:8000` | `services/ai-backend` base URL |

---

## Deployment profile

| Variable                        | Default       | Notes                                                                           |
| ------------------------------- | ------------- | ------------------------------------------------------------------------------- |
| `ENTERPRISE_DEPLOYMENT_PROFILE` | `development` | `development`, `saas`, `bank`, `government`; controls `dev_auth_bypass_allowed` |

---

## Observability / telemetry

| Variable                      | Default          | Notes                                                                                                   |
| ----------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------- |
| `OTEL_COLLECTOR_HTTP_URL`     | `` (empty)       | If set, the facade proxies browser OTEL traces to this collector at `POST /v1/telemetry/otlp/v1/traces` |
| `OTEL_SERVICE_NAME`           | `backend-facade` | OpenTelemetry service name                                                                              |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | —                | OTLP endpoint for server-side traces                                                                    |
| `LOG_LEVEL`                   | `INFO`           | Python log level                                                                                        |

---

## Session touch cache

The touch cache parameters are hardcoded in `auth.py` (not configurable via env vars):

| Parameter | Value       |
| --------- | ----------- |
| Max size  | 128 entries |
| TTL       | 30 seconds  |

These constants live in `_TOUCH_CACHE_TTL_SECONDS` and `_TOUCH_CACHE_MAX_SIZE`.
