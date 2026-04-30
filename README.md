# Enterprise Search

Enterprise Search is the workspace for a broader enterprise work surface: one product that helps executives and employees search, understand, and act across company systems such as Slack, Google Workspace, Atlassian, internal APIs, MCP servers, and enterprise knowledge stores.

This is one GitHub monorepo with multiple deployable components. The runtime architecture is microservice-style: each service owns its API, Docker image, local dependency environment, tests, and deployment path.

The workspace now includes initial deployable scaffolding for `apps/frontend`, `services/backend-facade`, `services/backend`, `services/ai-backend`, `packages/api-types`, and `packages/design-system`.

## Current And Target Repository Layout

Implemented paths are present today. Planned paths describe the target
architecture and should not be imported from or referenced by builds until they
exist.

```text
enterprise-search/
  apps/
    frontend/        # implemented
    mac/             # planned
    windows/         # planned
  services/
    backend-facade/  # implemented
    backend/         # implemented
    ai-backend/      # implemented
  packages/
    api-types/       # implemented
    design-system/   # implemented
    shared-config/   # planned
  infra/
    docker/
    compose.yaml
  docs/
    architecture/
    ci-cd/
    decisions/
  .cursor/
    rules/
  .github/
    workflows/
```

## Monorepo, Microservice Runtime

Monorepo and microservices are separate decisions. This repo should keep related product code together while allowing services to deploy independently.

- Monorepo: one GitHub repository, one PR can update app, API contract, service, and docs together.
- Microservice-style runtime: backend services are independently built, tested, containerized, and deployed.
- Shared packages: stable contracts and cross-cutting primitives only, not a place to hide business ownership.

## Components

- `services/ai-backend`: implemented AI orchestration backend for Deep Agents, LangGraph, LangChain tools, dynamic MCP loading, skills, context/memory management, subagents, streaming, and retrieval orchestration.
- `services/backend-facade`: implemented product-facing API surface that frontend and native apps call. It hides internal service topology.
- `services/backend`: implemented core backend slice for MCP registration, OAuth state, token storage, user skills, and audit events. Tenant auth, permissions, billing/admin workflows, broader product persistence, and operational jobs remain target backend responsibilities.
- `apps/frontend`: implemented web work surface for enterprise search, agent interaction, source review, workflow execution, and admin views.
- `apps/windows`: planned Windows desktop client for desktop workflows and enterprise distribution.
- `apps/mac`: planned macOS desktop client for executive workflows, desktop search, and notifications.
- `packages/api-types`: implemented shared API schemas and contract types.
- `packages/design-system`: implemented shared design tokens and UI primitives for web.
- `packages/shared-config`: planned shared lint, formatting, TypeScript, Python, and CI config where appropriate.

## System Direction

The product should feel like a trusted operating layer for enterprise work, not a simple keyword search box. The user should not need to know which system owns the data or which tool must be called. The platform should route requests through the right backend, respect permissions, stream progress, and return grounded, traceable answers.

## Service Boundaries

- Apps call `backend-facade`, not internal services directly.
- `backend-facade` owns product-facing APIs, request aggregation, response shaping, and app-compatible streaming surfaces.
- `backend` currently owns MCP registration, OAuth/token state, user skills, and audit events. It is the target home for tenants, auth integration, permissions, product persistence, admin workflows, and jobs.
- `ai-backend` owns agent orchestration, tools, skills, MCP, memory, subagents, streaming events, and retrieval orchestration.
- Shared packages hold stable contracts and generated clients. They should not contain hidden business logic that makes ownership ambiguous.

## Docker And CI/CD Direction

Each deployable component should have its own Docker image:

- `ghcr.io/<org>/enterprise-search-backend-facade`
- `ghcr.io/<org>/enterprise-search-backend`
- `ghcr.io/<org>/agent-runtime-backend`
- `ghcr.io/<org>/enterprise-search-frontend`

Each deployable component also owns its local dependency environment:

- `services/backend`: service-local Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, and `Dockerfile`; its Docker build uses the repo root as context for constants-only service contracts.
- `services/backend-facade`: service-local Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, and `Dockerfile`; its Docker build uses the repo root as context for constants-only service contracts.
- `services/ai-backend`: service-local Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, and `Dockerfile`; its Docker build uses the repo root as context for constants-only service contracts.
- `apps/frontend`: npm workspace dependency environment with its own `package.json`, Vite config, and `Dockerfile`; it must not use a Python service venv.

Do not run or test one service with another service's `.venv`. Create the target service's `.venv` from its own `requirements.txt` before running that component locally.

Starting CI/CD model:

- CI on every PR: lint, typecheck, unit tests, builds, and Docker build validation for changed components.
- Path-filtered workflows so unrelated apps/services do not rebuild unnecessarily.
- CD after merge to `main`: build and push service images to GitHub Container Registry.
- Staging deploy from `main`.
- Production deploy through GitHub Environments with manual approval.
- Desktop apps use platform-specific pipelines later: macOS runners for Mac builds, and Windows runners for Windows builds.

## Current Status

The workspace now includes initial scaffolding for `apps/frontend`, `services/backend-facade`, `services/backend`, `services/ai-backend`, `packages/api-types`, and `packages/design-system`.

Start there:

- `apps/README.md`
- `packages/README.md`
- `services/ai-backend/README.md`
- `services/ai-backend/docs/README.md`
- `docs/architecture/workspace-topology.md`
- `docs/architecture/service-boundaries.md`

## Repo Rules

- Keep service boundaries clear. Do not put frontend, facade, core backend, or native app concerns into `services/ai-backend`.
- Prefer stable APIs and generated clients between components over direct cross-service imports.
- Do not import implementation code across `apps/*` or `services/*`. Cross-component integration must use HTTP APIs, queues/events, constants-only service contracts, or generated contracts from `packages/api-types`.
- Do not add a sibling service directory to `PYTHONPATH` or use relative imports to reach another deployable component.
- Each deployable component owns its dependency environment and Dockerfile:
  - Python services use a service-local `.venv`, `requirements.txt`, and `Dockerfile`.
  - The web frontend uses its own npm workspace environment, `package.json`/`package-lock.json`, and `Dockerfile`.
- Document responsibilities before implementation when introducing a new component.
- Treat permissions, auth context, and tenant boundaries as cross-cutting product requirements.
- Every implementation should include focused unit tests and edge-case coverage appropriate to its component.
- Do not create shared packages just to avoid a small amount of duplication; share only stable contracts and truly cross-cutting primitives.

