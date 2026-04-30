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

Internal routes require service authentication through the
`X-Enterprise-Service-Token` header. The expected value is loaded from
`ENTERPRISE_SERVICE_TOKEN`; production startup and requests must fail closed if
that value is not configured. Internal callers should also forward
`X-Enterprise-Org-Id` and `X-Enterprise-User-Id` so tenant and user scope is
derived from authenticated service context, not browser-controlled parameters.

Deployment network policy must keep `/internal/v1/*` reachable only from the
private service network. Public ingress, load balancer rules, and API gateway
routes must deny direct browser or internet access to this path prefix.

## MCP Routes

| Method | Path                                                  | Purpose                                                  |
| ------ | ----------------------------------------------------- | -------------------------------------------------------- |
| `GET`  | `/internal/v1/mcp/cards`                              | List enabled MCP server cards for an org/user scope      |
| `POST` | `/internal/v1/mcp/servers/{server_id}/auth/start`     | Start OAuth for a server on behalf of an internal caller |
| `POST` | `/internal/v1/mcp/servers/{server_id}/client-session` | Create a backend-only MCP client session                 |
| `POST` | `/internal/v1/mcp/servers/{server_id}/test-token`     | Upsert a token for local/test flows                      |

All MCP routes require scoped identity from trusted service headers in
production. Local development may still pass `org_id` and `user_id` query/body
values when `ENTERPRISE_SERVICE_TOKEN` is unset.

Internal MCP responses may include connection material that apps should not see.
They must not be forwarded through the product facade.

## Skill Routes

| Method | Path                                 | Purpose                                             |
| ------ | ------------------------------------ | --------------------------------------------------- |
| `GET`  | `/internal/v1/skills/cards`          | List enabled skill cards for runtime selection      |
| `GET`  | `/internal/v1/skills/{skill_id}`     | Fetch the full internal skill bundle by ID          |
| `GET`  | `/internal/v1/skills/by-name/{name}` | Fetch the full internal skill bundle by stable name |

Skill cards are summaries used for selection. Skill bundles include the
model-consumable markdown and metadata required by the runtime.

## Failure Semantics

- Missing or invalid service tokens should return `401`.
- Missing production token configuration should return `503`.
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
