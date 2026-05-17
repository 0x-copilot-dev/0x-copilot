# Service Boundaries

## Why Boundaries Matter

This product will grow across web, Mac, Windows, backend services, AI orchestration, enterprise connectors, and deployment infrastructure. Clear ownership prevents every component from becoming a dependency knot.

## Component Responsibilities

This section describes both implemented ownership and target ownership. Planned
components are marked explicitly so current build and import paths stay tied to
what exists on disk.

### `apps/frontend`

Implemented. Owns the web work surface: search experience, agent interaction,
source review, admin screens, and web-specific UX. It talks to `backend-facade`.

### `apps/mac`

Planned. Owns the macOS client: desktop search, executive workflows,
notifications, and macOS-specific capabilities. It talks to `backend-facade`.

### `apps/windows`

Planned. Owns the Windows desktop client: enterprise desktop workflows, native
shell integration, and distribution concerns. It talks to `backend-facade`.

### `services/backend-facade`

Implemented. Owns the public product API for apps. It shapes responses,
aggregates backend service calls, handles app-compatible streaming, and
preserves a stable API even if internal services change.

It should not own AI orchestration, product persistence, or connector side effects.

### `services/backend`

Implemented current slice: MCP registration, OAuth state, token storage, user
skills, and audit events. Target ownership: tenants, user/org mapping, auth
integration, permissions, product database, admin workflows, background jobs,
and audit records.

It should not own LLM agent orchestration or UI presentation state.

### `services/ai-backend`

Implemented. Owns AI orchestration: Deep Agents runtime, LangGraph execution,
LangChain tool wiring, dynamic tool loading, dynamic MCP loading, skills,
context/memory management, subagents, streaming events, and retrieval
orchestration.

It should not own tenant auth, billing/admin state, product persistence, or app-specific presentation logic.

### `packages/api-types`

Implemented. Owns stable TypeScript schemas and public contracts between apps
and services. Generated clients are target direction, not current behavior.

### `packages/chat-transport`

Implemented. Owns the substrate-portable `Transport` port — HTTP request

- Server-Sent Event subscription — used by every app surface that talks to
  `backend-facade`. Ships the web implementation (`WebTransport`, deferred-lookup
  `fetch`) so the host app only constructs an instance and feeds it to chat-surface.
  The desktop substrate will ship a parallel `WebviewTransport` (postMessage RPC to
  the VS Code extension host) without any change to chat-surface or features.

Boundary: this package must not import from any app, service, or design surface.
Auth (`UnauthorizedError`), domain envelopes (`RuntimeEventEnvelope` via
`api-types`), and HTTP framing live here. Bearer storage does NOT — secrets are
out of scope; the host substrate owns them.

### `packages/chat-surface`

Implemented. Owns the substrate-portable chat UI primitives — message renderers
(`PlainText`, `Reasoning`, citation chips), the streaming-cursor contract, the
citation remark plugin, and the cross-cutting ports (`Router`, `KeyValueStore`,
`PresenceSignal`) plus their `ChatShell` mount. Components are headless and
prop-driven; substrate-coupled state (context lookups, web portals) lives in
the consuming app's `features/` as a thin adapter that resolves data via hooks
and delegates rendering to chat-surface.

Boundary: enforced by ESLint — chat-surface cannot reference bare browser
primitives (`window.*`, `document.*`, `localStorage`, `fetch`, …) or import from
any `apps/*`. The package's own web reference implementations (e.g.
`LocalStorageKeyValueStore`) intentionally use `globalThis.X` member access; the
prefix marks "I know this is a substrate touchpoint." Anything beyond that
exception goes through a port.

### `packages/shared-config`

Planned. Owns shared lint, formatting, testing, Docker, and CI config when that
config is genuinely common.

### `packages/design-system`

Implemented for web. Owns stable design tokens and shared UI primitives. Do not
force native and web UI into one abstraction before the product needs it.

## Shared Package Rule

Use shared packages for stable contracts and cross-cutting primitives. Do not create a shared package merely to avoid a small amount of duplication. Premature sharing hides ownership and slows future changes.

## Cross-Component Dependency Rule

Deployable apps and services must not import one another's implementation code.
Allowed integration mechanisms are:

- HTTP APIs exposed by the owning service.
- Queues, jobs, or events with documented payload contracts.
- Generated clients and stable contract types from `packages/api-types`.
- Shared configuration packages that contain no business logic.

Do not add sibling components to `PYTHONPATH`, use relative imports across
deployable boundaries, or reuse another service's virtual environment. Those are
boundary violations even inside the monorepo.

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
