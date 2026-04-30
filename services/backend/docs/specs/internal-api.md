# Spec: Backend Internal API

## Purpose

Document backend routes that are meant for trusted internal service callers, not
browser or native app clients. These routes expose model-ready MCP and skill
metadata and backend-only session helpers.

## Boundary

- Base path: `/internal/v1/*`
- Owner: `services/backend`
- Expected consumers: internal services such as `services/ai-backend`
- Product-facing exposure: none. `services/backend-facade` should not proxy
  these routes unless a future accepted spec changes the boundary.

Current implementation relies on network and deployment trust plus explicit
`org_id` and `user_id` scope parameters. There is no dedicated auth middleware
on these routes yet. Before production exposure beyond a trusted private
network, add service authentication and authorization checks.

## MCP Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/internal/v1/mcp/cards` | List enabled MCP server cards for an org/user scope |
| `POST` | `/internal/v1/mcp/servers/{server_id}/auth/start` | Start OAuth for a server on behalf of an internal caller |
| `POST` | `/internal/v1/mcp/servers/{server_id}/client-session` | Create a backend-only MCP client session |
| `POST` | `/internal/v1/mcp/servers/{server_id}/test-token` | Upsert a token for local/test flows |

All MCP routes require the caller to provide scoped identity either through the
Pydantic body or `org_id` and `user_id` query parameters.

Internal MCP responses may include connection material that apps should not see.
They must not be forwarded through the product facade.

## Skill Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/internal/v1/skills/cards` | List enabled skill cards for runtime selection |
| `GET` | `/internal/v1/skills/{skill_id}` | Fetch the full internal skill bundle by ID |
| `GET` | `/internal/v1/skills/by-name/{name}` | Fetch the full internal skill bundle by stable name |

Skill cards are summaries used for selection. Skill bundles include the
model-consumable markdown and metadata required by the runtime.

## Failure Semantics

- Missing or invalid scope parameters should return FastAPI validation errors.
- Unknown or inaccessible records should return `404`.
- Unsupported auth flows should return `400`.
- Internal callers should treat `404` as either not found or not visible in the
  provided org/user scope.

## Change Policy

When changing these routes:

1. Update `backend_app.contracts` and route tests.
2. Update this spec.
3. Update `services/ai-backend` callers or specs if runtime consumption changes.
4. Do not expose the route through `backend-facade` without updating the product
   API surface spec and service-boundary docs.
