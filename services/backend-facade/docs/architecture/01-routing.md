# Routing — backend-facade

How a request travels from the caller through the facade to the upstream service.

See also:

- [00-system-map.md](00-system-map.md) — module map
- [02-auth-identity.md](02-auth-identity.md) — bearer verification and session touch
- [reference/api-surface.md](../reference/api-surface.md) — full route table with targets

---

## Request path

```
Browser / Desktop / API client
  │
  │  Authorization: Bearer <token>
  ▼
backend-facade:8200
  │
  ├── authenticate_request(request) → AuthenticatedIdentity
  │     ↳ HMAC verify (local, fast)
  │     ↳ optionally: verify_with_touch → backend session touch (cached 30s)
  │
  ├── _outbound_headers(identity)
  │     ↳ X-Enterprise-Service-Token: <ENTERPRISE_SERVICE_TOKEN>
  │     ↳ x-enterprise-org-id: <org_id>
  │     ↳ x-enterprise-user-id: <user_id>
  │     ↳ x-enterprise-roles: <comma-separated>
  │     ↳ x-enterprise-permission-scopes: <comma-separated>
  │     ↳ x-enterprise-connector-scopes: <JSON>
  │     ↳ x-request-id: <correlation ID>
  │
  ├── forward_json(target="backend")  → backend:8100
  │     Routes: /v1/mcp/*, /v1/skills/*, /v1/api-keys/*
  │
  └── forward_json(target="ai_backend") → ai-backend:8000
        Routes: /v1/agent/*, /v1/usage/*, /v1/budgets/*, /v1/retention/*
```

---

## Routing decision

The route handler in `app.py` determines the upstream target statically (by URL prefix).
There is no dynamic routing logic; every route specifies `target="backend"` or `target="ai_backend"`.

| URL prefix             | Target                             | Notes                                                  |
| ---------------------- | ---------------------------------- | ------------------------------------------------------ |
| `/v1/mcp/*`            | `backend`                          | MCP server registry and OAuth                          |
| `/v1/skills/*`         | `backend` (list merges ai-backend) | Skill CRUD (see skills merge below)                    |
| `/v1/api-keys/*`       | `backend`                          | API key management                                     |
| `/v1/agent/*`          | `ai_backend`                       | Conversations, runs, events, approvals, drafts, shares |
| `/v1/usage/*`          | `ai_backend`                       | Token usage and cost analytics                         |
| `/v1/budgets/*`        | `ai_backend`                       | Workspace budgets                                      |
| `/v1/retention/*`      | `ai_backend`                       | Effective retention TTL                                |
| `/v1/session`          | Inline (facade)                    | Returns the verified identity from the bearer          |
| `/v1/health`           | Inline (facade)                    | Facade health check                                    |
| `/v1/telemetry/otlp/*` | `otel_collector_url`               | OTEL trace relay                                       |
| `/v1/dev/*`            | `backend` (dev only)               | Dev IdP mint/personas proxy                            |

---

## Skills merge (`GET /v1/skills`)

This is the one route where the facade calls two upstreams and merges:

1. `GET /v1/skills` → `backend` → user/preloaded skills (with markdown)
2. `GET /internal/v1/skills/system` → `ai-backend` → system skills (from ai-backend filesystem)

System skills lead in the merged response (rendered at the top in the UI).
`_coerce_skill_list()` tolerates shape variations from either upstream without raising 500.

---

## Identity injection

Before forwarding, the facade calls `identity.scoped_payload(payload)` for POST/PUT/PATCH
bodies. This overwrites `org_id` and `user_id` in the payload with the verified identity.
The caller cannot inject a different org or user.

For GET requests, `identity.scoped_params()` appends `org_id` and `user_id` as query params.

---

## SSE streaming (`GET /v1/agent/runs/{run_id}/stream`)

This route is special — the response is a persistent `text/event-stream`. The facade:

1. Opens an `httpx.AsyncClient(timeout=None)` to avoid a streaming timeout.
2. Calls `client.send(..., stream=True)` to ai-backend.
3. Returns a `StreamingResponse` that iterates `upstream.aiter_bytes()`.
4. Checks `await request.is_disconnected()` each iteration to close when the browser navigates away.
5. Always closes both the upstream and the client in a `finally` block.

---

## Error propagation

`_upstream_error_detail(response)` extracts the upstream error body:

- If JSON with a `detail` key → return `detail` value
- If JSON without `detail` → return the whole JSON payload
- If not JSON → return raw text

This detail is set as the `HTTPException.detail` forwarded to the client. Internal
upstream errors (e.g., database errors) are surfaced as the upstream's HTTP status code.

---

## Timeout defaults

| Call type                             | Timeout         |
| ------------------------------------- | --------------- |
| Regular JSON forward (`forward_json`) | 30s             |
| Backend session touch                 | 5s              |
| Backend API key verify                | 5s              |
| SSE streaming                         | None (infinite) |
| OTEL trace relay                      | 15s             |
| Dev IdP proxy                         | 10s             |
