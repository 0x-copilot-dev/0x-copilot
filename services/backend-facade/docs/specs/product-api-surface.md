# Spec: Product API Surface

## Purpose

Document the app-facing API paths exposed by `services/backend-facade` and the
upstream owner for each path family.

Apps should treat this service as the stable product API surface. Internal
service routes and implementation modules are not part of the app contract.

## Route Families

| Method and path                                          | Upstream owner        | Purpose                              |
| -------------------------------------------------------- | --------------------- | ------------------------------------ |
| `POST /v1/mcp/servers`                                   | `services/backend`    | Create an MCP server registration    |
| `GET /v1/mcp/servers`                                    | `services/backend`    | List MCP servers for org/user scope  |
| `PATCH /v1/mcp/servers/{server_id}`                      | `services/backend`    | Update display or enabled state      |
| `DELETE /v1/mcp/servers/{server_id}`                     | `services/backend`    | Delete an MCP server                 |
| `POST /v1/mcp/servers/{server_id}/auth/start`            | `services/backend`    | Start OAuth                          |
| `POST /v1/mcp/servers/{server_id}/auth/skip`             | `services/backend`    | Mark auth as skipped                 |
| `GET /v1/mcp/oauth/callback`                             | `services/backend`    | Complete OAuth callback              |
| `POST /v1/skills`                                        | `services/backend`    | Create a skill                       |
| `GET /v1/skills`                                         | `services/backend`    | List skills                          |
| `GET /v1/skills/{skill_id}`                              | `services/backend`    | Fetch a skill                        |
| `PUT /v1/skills/{skill_id}`                              | `services/backend`    | Replace a skill                      |
| `DELETE /v1/skills/{skill_id}`                           | `services/backend`    | Delete a skill                       |
| `POST /v1/agent/conversations`                           | `services/ai-backend` | Create or resume a conversation      |
| `GET /v1/agent/conversations/{conversation_id}`          | `services/ai-backend` | Fetch conversation metadata          |
| `GET /v1/agent/conversations/{conversation_id}/messages` | `services/ai-backend` | Fetch conversation messages          |
| `POST /v1/agent/runs`                                    | `services/ai-backend` | Create a runtime run                 |
| `GET /v1/agent/runs/{run_id}`                            | `services/ai-backend` | Fetch run state                      |
| `GET /v1/agent/runs/{run_id}/events`                     | `services/ai-backend` | Replay persisted run events          |
| `GET /v1/agent/runs/{run_id}/stream`                     | `services/ai-backend` | Stream run events as SSE             |
| `POST /v1/agent/runs/{run_id}/cancel`                    | `services/ai-backend` | Request run cancellation             |
| `POST /v1/agent/approvals/{approval_id}/decision`        | `services/ai-backend` | Resolve an approval                  |
| `DELETE /v1/agent/history`                               | `services/ai-backend` | Tombstone user-visible agent history |

## Non-Surface Routes

`/internal/v1/*` routes from upstream services are not exposed by the facade.
They are reserved for trusted service-to-service calls and must not be called by
apps.

## Versioning

All current product routes use the `/v1` prefix. Breaking app-facing changes
must either preserve compatibility in `/v1` or introduce a documented migration
path before changing route shape, status behavior, or payload semantics.

## Contract Ownership

The facade is currently a pass-through layer. Upstream services own runtime
validation through Pydantic contracts, and frontend TypeScript shapes live in
`packages/api-types`.

If the facade starts reshaping responses, it must own explicit Pydantic response
models and update `packages/api-types` in the same change.

## Failure Behavior

- Upstream error statuses are propagated with upstream response text.
- Expected empty responses, such as successful deletes, return `204`.
- Expected JSON responses must be JSON objects; other JSON payloads become
  `502 Bad Gateway`.
- SSE streams are passed through as `text/event-stream`.

The MCP OAuth callback forwards standard OAuth success and error query
parameters. Successful callbacks include `state` and `code`; denied or failed
callbacks include `state`, `error`, and optionally `error_description`.
