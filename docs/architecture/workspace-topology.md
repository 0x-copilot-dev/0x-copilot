# Workspace Topology

## Architecture Model

Enterprise Search should be developed as one GitHub monorepo with multiple deployable services and apps. The code lives together so product changes can move coherently, but services still have clear runtime boundaries.

```mermaid
flowchart TD
  WebFrontend[Web Frontend] --> BackendFacade[Backend Facade]
  MacApp[Mac App] --> BackendFacade
  WindowsApp[Windows App] --> BackendFacade
  BackendFacade --> CoreBackend[Core Backend]
  BackendFacade --> AiBackend[AI Backend]
  CoreBackend --> ProductDB[Product Database]
  AiBackend --> McpServers[MCP Servers]
  AiBackend --> EnterpriseConnectors[Enterprise Connectors]
  AiBackend --> VectorStores[Vector Stores]
```

## Target Layout

```text
enterprise-search/
  apps/
    frontend/
    mac/
    windows/
  services/
    backend-facade/
    backend/
    ai-backend/
  packages/
    api-types/
    shared-config/
    design-system/
  infra/
    docker/
    compose.yaml
  docs/
    architecture/
    ci-cd/
    decisions/
```

`services/ai-backend` is the canonical AI backend service path. Do not move service directories casually; any future service move must update docs, rules, CI paths, imports, and setup commands together.

## Allowed Call Direction

- Apps call `backend-facade`.
- `backend-facade` calls `backend` and `ai-backend`.
- `backend` owns product state and may emit events/jobs for other services.
- `ai-backend` may call MCP servers, enterprise connectors, vector stores, and LLM providers through typed ports.
- Shared packages provide contracts and generated clients, not hidden runtime coupling.

Allowed call direction means runtime calls over APIs, queues, or documented events.
It does not permit direct imports across app/service implementation packages.
It also does not permit running a component with a sibling service's `.venv` or
adding another deployable component's `src` directory to `PYTHONPATH`.

## Disallowed Shortcuts

- Apps must not call `ai-backend` directly unless a future approved spec creates an exception for streaming.
- Apps and services must not import code from sibling apps or services.
- Apps and services must not share local virtual environments or dependency manifests.
- `ai-backend` must not own tenant auth, billing/admin workflows, or product persistence.
- `backend-facade` must not absorb AI orchestration logic.
- Shared packages must not become dumping grounds for business logic.

