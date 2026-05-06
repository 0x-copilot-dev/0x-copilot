# Backend Facade Architecture

`services/backend-facade` is the product-facing API surface for apps. It hides
the internal service topology and forwards app-compatible HTTP and SSE calls to
the service that owns the behavior.

The facade does not own AI orchestration, product persistence, connector side
effects, or internal service data stores.

## Module Map

- `backend_facade.app`: FastAPI app, route registration, forwarding helpers, and
  SSE passthrough.
- `backend_facade.settings`: environment-backed upstream URLs.

## Upstream Settings

| Setting          | Default                 | Owner                 |
| ---------------- | ----------------------- | --------------------- |
| `BACKEND_URL`    | `http://127.0.0.1:8100` | `services/backend`    |
| `AI_BACKEND_URL` | `http://127.0.0.1:8000` | `services/ai-backend` |

Trailing slashes are stripped when settings load.

## Forwarding Matrix

| Facade route family        | Upstream         | Notes                                         |
| -------------------------- | ---------------- | --------------------------------------------- |
| `/v1/mcp/*`                | `BACKEND_URL`    | MCP registry, auth start/skip, OAuth callback |
| `/v1/skills*`              | `BACKEND_URL`    | Skill registry public API                     |
| `/v1/agent/conversations*` | `AI_BACKEND_URL` | Conversation creation, metadata, messages     |
| `/v1/agent/runs*`          | `AI_BACKEND_URL` | Run creation, state, events, cancel, stream   |
| `/v1/agent/approvals/*`    | `AI_BACKEND_URL` | Approval decisions                            |

The facade intentionally does not expose backend `/internal/v1/*` routes.

## JSON Forwarding

JSON routes use an `httpx.AsyncClient` with a 30 second timeout. Upstream
non-2xx responses are returned as `HTTPException` with the upstream status and
body text. For routes that expect JSON objects, the facade requires the upstream
payload to be a JSON object; non-object JSON becomes `502 Bad Gateway`. Routes
whose public contract is a top-level array opt out explicitly at the forwarding
call site.

The current facade uses `dict[str, object]` payloads and relies on upstream
services for validation. If response shaping or app-specific validation becomes
more than pass-through, introduce explicit Pydantic models and keep
`packages/api-types` aligned.

## Streaming

`GET /v1/agent/runs/{run_id}/stream` opens an upstream streaming request to
`AI_BACKEND_URL` with no client timeout and returns `text/event-stream` to the
app. The facade yields bytes from the upstream stream until the client
disconnects or the upstream stream ends.

## Engineering Invariants

- Apps should call this service, not internal backend services.
- The facade may call `services/backend` and `services/ai-backend` over HTTP but
  must not import their Python modules.
- Do not put AI orchestration or durable product persistence here.
- Do not expose `/internal/v1/*` without updating service-boundary docs and the
  product API surface spec.
- Keep route additions reflected in `docs/specs/product-api-surface.md` and
  `packages/api-types` when app contracts change.
