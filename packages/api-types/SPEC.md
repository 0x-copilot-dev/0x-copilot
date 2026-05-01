# Spec: TypeScript API Contracts

## Purpose

Provide TypeScript shapes for app-facing API payloads and runtime events so
frontend code can consume service contracts without importing service
implementation code.

## Scope

This package may define:

- Public MCP registry request and response shapes.
- Public skill registry request and response shapes.
- Agent conversation, message, run, approval, and event shapes.
- Frontend-visible enum unions and metadata maps.

This package must not define:

- Backend persistence records that are not public API responses.
- `/internal/v1/*` service-to-service contracts.
- HTTP clients, fetch wrappers, or route ownership logic.
- UI-specific view models.

## Current Source Of Truth

The current source of truth is dual-written:

- Python Pydantic contracts validate server behavior.
- TypeScript interfaces validate frontend usage.

Until generation exists, every public contract change should update both sides
in the same PR and include focused tests or typechecks for the changed surface.
Focused drift tests currently compare runtime event, event source, activity, and
run status enum constants between the Python server and this package.

## Alignment Matrix

| TypeScript surface         | Server owner          | Product route owner       |
| -------------------------- | --------------------- | ------------------------- |
| MCP types                  | `services/backend`    | `services/backend-facade` |
| Skill types                | `services/backend`    | `services/backend-facade` |
| Conversation and run types | `services/ai-backend` | `services/backend-facade` |
| Runtime event types        | `services/ai-backend` | `services/backend-facade` |
| Approval types             | `services/ai-backend` | `services/backend-facade` |

## Compatibility Policy

- Additive optional fields are usually compatible.
- New required request fields need a migration plan for app callers.
- Removed response fields need a frontend cleanup in the same change.
- Enum additions need UI fallback behavior before rollout.
- Enum removals or renames are breaking and require a versioned migration.

## Assistant Performance Metrics

Agent runtime responses may include `AssistantPerformanceMetrics` under
`metadata.performance_metrics` for persisted assistant messages and under
`payload.performance_metrics` for `final_response` / `run_completed` events.
Timing and chunk fields are server-computed. Token fields are optional and must
represent exact provider usage metadata under the `usage` object; clients should
omit token UI when those fields are absent.

## MCP OAuth Client Configuration

`CreateMcpServerRequest.oauth_client` and
`UpdateMcpServerRequest.oauth_client` are optional advanced fields for generic
MCP servers that require a pre-registered OAuth client instead of dynamic client
registration. Public `McpServer` responses expose only
`oauth_client_configured`; raw or encrypted client secrets are backend-owned and
must never appear in app-facing payloads.

## Future Generation

If OpenAPI or another contract generator becomes canonical, generated files
should live under a clearly named generated path and local hand-written helper
types should remain separate. Do not mix generated and hand-authored code in a
way that makes regeneration unsafe.
