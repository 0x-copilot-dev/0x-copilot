# Frontend Architecture

The frontend is the web work surface for Enterprise Search. It owns browser UI,
screen-level state, and client-side interaction flows. It does not own product
persistence, AI orchestration, connector side effects, or service credentials.

## Runtime Boundary

The app calls `backend-facade` through `/v1/*`.

| Frontend API module | Route family | Upstream owner |
| --- | --- | --- |
| `src/api/agentApi.ts` | `/v1/agent/*` | `services/ai-backend`, reached through `services/backend-facade` |
| `src/api/mcpApi.ts` | `/v1/mcp/*` | `services/backend`, reached through `services/backend-facade` |
| `src/skillsApi.ts` | `/v1/skills*` | `services/backend`, reached through `services/backend-facade` |

Use `src/api/*` as the canonical API layer for new work. Legacy root-level API
helpers should not gain new callers; move behavior into `src/api/*` first.

## App Structure

- `src/app/`: application shell, routing decisions, and page composition.
- `src/features/chat/`: chat and agent interaction UI.
- `src/features/connectors/`: MCP server connection and OAuth callback flows.
- `src/features/settings/`: settings surface.
- `src/api/`: HTTP/SSE helpers and typed route clients.

Shared contracts come from `@enterprise-search/api-types`. Shared UI primitives
and theme behavior come from `@enterprise-search/design-system`.

## Dev And Production Routing

During local development, Vite proxies `/v1` to `http://127.0.0.1:8200`, the
default `backend-facade` port.

In the container image, nginx serves the static SPA and falls back to
`index.html`. It does not proxy `/v1`. Production deployment must therefore
route `/v1/*` to `backend-facade` at the ingress, gateway, or hosting layer.

## Identity State

`src/api/config.ts` contains temporary default org/user identifiers. Treat them
as local-development placeholders until auth and tenant context are owned by
`services/backend` and surfaced through `backend-facade`.

## OAuth Callback

The connector OAuth callback is a frontend route state, not a separate backend
entry point. It should complete by calling the facade-backed MCP APIs and then
return control to the connectors UI.

## Engineering Invariants

- Do not import service implementation code into the app.
- Do not call `services/backend` or `services/ai-backend` directly from browser
  code.
- Add frontend-facing contract types to `@enterprise-search/api-types` before
  broadening request or response shapes.
- Keep app-specific behavior in `apps/frontend`; only promote UI primitives to
  `packages/design-system` after they are stable and reusable.
