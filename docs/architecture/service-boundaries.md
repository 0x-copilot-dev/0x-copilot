# Service Boundaries

## Why Boundaries Matter

This product will grow across web, Mac, Windows, backend services, AI orchestration, enterprise connectors, and deployment infrastructure. Clear ownership prevents every component from becoming a dependency knot.

## Component Responsibilities

### `apps/frontend`

Owns the web work surface: search experience, agent interaction, source review, admin screens, and web-specific UX. It talks to `backend-facade`.

### `apps/mac`

Owns the macOS client: desktop search, executive workflows, notifications, and macOS-specific capabilities. It talks to `backend-facade`.

### `apps/windows`

Owns the Windows desktop client: enterprise desktop workflows, native shell integration, and distribution concerns. It talks to `backend-facade`.

### `services/backend-facade`

Owns the public product API for apps. It shapes responses, aggregates backend service calls, handles app-compatible streaming, and preserves a stable API even if internal services change.

It should not own AI orchestration, product persistence, or connector side effects.

### `services/backend`

Owns core product backend concerns: tenants, user/org mapping, auth integration, permissions, product database, admin workflows, background jobs, and audit records.

It should not own LLM agent orchestration or UI presentation state.

### `services/ai-backend`

Owns AI orchestration: Deep Agents runtime, LangGraph execution, LangChain tool wiring, dynamic tool loading, dynamic MCP loading, skills, context/memory management, subagents, streaming events, and retrieval orchestration.

It should not own tenant auth, billing/admin state, product persistence, or app-specific presentation logic.

### `packages/api-types`

Owns stable schemas, generated clients, and public contracts between apps and services.

### `packages/shared-config`

Owns shared lint, formatting, testing, Docker, and CI config when that config is genuinely common.

### `packages/design-system`

Owns stable design tokens and shared UI primitives. Do not force native and web UI into one abstraction before the product needs it.

## Shared Package Rule

Use shared packages for stable contracts and cross-cutting primitives. Do not create a shared package merely to avoid a small amount of duplication. Premature sharing hides ownership and slows future changes.

## Cross-Component Dependency Rule

Deployable apps and services must not import one another's implementation code.
Allowed integration mechanisms are:

- HTTP APIs exposed by the owning service.
- Queues, jobs, or events with documented payload contracts.
- Generated clients and stable contract types from `packages/api-types`.
- Shared configuration packages that contain no business logic.

Examples:

- `apps/frontend` may import generated API types, but must call `backend-facade` over HTTP/SSE.
- `services/backend-facade` may call `services/backend` and `services/ai-backend` APIs, but must not import their Python modules.
- `services/ai-backend` may call backend-owned MCP registry APIs through typed clients, but must not import backend store or auth code.

## Contract Rule

Every service boundary needs:

- A typed API contract.
- A versioning or migration story.
- Unit tests around serialization and validation.
- A component-local dependency environment, Dockerfile, and deploy story.
- Observability and safe error handling.

