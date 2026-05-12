# System Map — backend-facade

Module-to-file-to-responsibility reference.

See also: [01-routing.md](01-routing.md) for how modules cooperate at request time.

---

## Top-level process

Single FastAPI process: `backend_facade/app.py`. No worker process, no background tasks.
All state is in the per-process `_TouchCache` LRU (max 128 entries; 30s TTL).

---

## Module map

### `backend_facade/` — root modules

| File                    | Owns                                                                                                                                           |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.py`                | FastAPI app assembly; all route definitions inline; `create_app()` factory; `forward_json()` and SSE streaming helper                          |
| `auth.py`               | `FacadeAuthenticator` — HMAC bearer verify; `_TouchCache` LRU; `verify_with_touch()` + API key path; `StepUpRequired`; `requires_recent_mfa()` |
| `settings.py`           | `FacadeSettings` — `backend_url`, `ai_backend_url`, `otel_collector_url` from env vars                                                         |
| `deployment_profile.py` | `DeploymentProfile` + `resolve_or_exit()` — per-profile safety defaults; controls dev-IdP proxy registration                                   |

---

### `backend_facade/routes/`

| File        | Prefix                              | Notes                                                                 |
| ----------- | ----------------------------------- | --------------------------------------------------------------------- |
| `health.py` | `/v1/health`, `/healthz`, `/readyz` | Public; returns `{service, deployment_profile, feature_toggles_hash}` |

---

### Registered route modules (called from `app.py`)

| Function                         | Registers routes      | Proxy target                                            |
| -------------------------------- | --------------------- | ------------------------------------------------------- |
| `register_auth_routes(app)`      | `auth_routes.py`      | Auth lifecycle: OIDC/SAML/local login, logout, sessions |
| `register_me_routes(app)`        | `me_routes.py`        | `/v1/me/*` profile, avatar, MFA, preferences            |
| `register_audit_routes(app)`     | `audit_routes.py`     | `/v1/audit` read surface                                |
| `register_scim_routes(app)`      | `scim_routes.py`      | `/scim/v2/*` SCIM proxy                                 |
| `register_workspace_routes(app)` | `workspace_routes.py` | `/v1/workspace/*` org admin                             |

---

### `backend_facade/observability/`

| File                 | Owns                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------ |
| `request_context.py` | `RequestContextMiddleware` — per-request correlation ID + trace context              |
| `log_config.py`      | Logging configuration                                                                |
| `log_event.py`       | Access log emission via `emit_access_log`                                            |
| `otel.py`            | `TelemetryBootstrap` — OTEL SDK init, httpx instrumentation, FastAPI instrumentation |

---

## Key helpers in `app.py`

### `forward_json(app, method, path, *, target, params, json, identity)`

The core proxy helper. Selects `backend_url` or `ai_backend_url` based on `target`, sends
the request with `_outbound_headers(identity)`, and:

- Returns `{}` for 204 / empty-body responses (avoids `JSONDecodeError`).
- Raises `HTTPException` preserving upstream error detail.

### `stream_run(request, run_id, after_sequence)` (inline in `app.py`)

Special-case SSE path: opens a persistent `httpx.AsyncClient` in streaming mode to
`ai_backend_url/v1/agent/runs/{run_id}/stream`. Relays bytes as `text/event-stream`.
Closes the upstream connection when the client disconnects (`request.is_disconnected()`).

### `_outbound_headers(identity)`

Combines `FacadeAuthenticator.service_headers(identity)` (service token + identity claims)
with the current W3C trace context from `RequestContextMiddleware`.

---

## Import rules

- **No import of `services/backend/src`** or `services/ai-backend/src` — ever.
- Cross-service work is HTTP-only via `httpx`.
- Never add sibling services to `PYTHONPATH`.

---

## Settings

`FacadeSettings.load()` reads:

- `BACKEND_URL` (default `http://127.0.0.1:8100`)
- `AI_BACKEND_URL` (default `http://127.0.0.1:8000`)
- `OTEL_COLLECTOR_HTTP_URL` (default empty)
