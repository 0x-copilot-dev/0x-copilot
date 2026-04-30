# Enterprise Search

Enterprise Search is the workspace for a broader enterprise work surface: one product that helps executives and employees search, understand, and act across company systems such as Slack, Google Workspace, Atlassian, internal APIs, MCP servers, and enterprise knowledge stores.

This should be one GitHub monorepo with multiple deployable components. The runtime architecture can still be microservice-style: each service owns its API, Docker image, local dependency environment, tests, and deployment path.

Today only `services/ai-backend` exists. The other apps, services, packages, and infrastructure folders will arrive one at a time as their responsibilities become concrete.

## Target Repository Layout

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

## Planned Components

- `services/ai-backend`: AI orchestration backend for Deep Agents, LangGraph, LangChain tools, dynamic MCP loading, skills, context/memory management, subagents, streaming, and retrieval orchestration.
- `services/backend-facade`: stable product-facing API surface that frontend and native apps call. It hides internal service topology.
- `services/backend`: core backend services for product data, persistence, tenant/auth integration, permissions, billing/admin workflows, and operational jobs.
- `apps/frontend`: web work surface for enterprise search, agent interaction, source review, workflow execution, and admin views.
- `apps/windows`: Windows desktop client for desktop workflows and enterprise distribution.
- `apps/mac`: macOS desktop client for executive workflows, desktop search, and notifications.
- `packages/api-types`: shared API schemas, generated clients, and contract types.
- `packages/shared-config`: shared lint, formatting, TypeScript, Python, and CI config where appropriate.
- `packages/design-system`: shared design tokens and UI primitives when stable enough to share.

## System Direction

The product should feel like a trusted operating layer for enterprise work, not a simple keyword search box. The user should not need to know which system owns the data or which tool must be called. The platform should route requests through the right backend, respect permissions, stream progress, and return grounded, traceable answers.

## Service Boundaries

- Apps call `backend-facade`, not internal services directly.
- `backend-facade` owns product-facing APIs, request aggregation, response shaping, and app-compatible streaming surfaces.
- `backend` owns tenants, auth integration, permissions, product persistence, admin workflows, and jobs.
- `ai-backend` owns agent orchestration, tools, skills, MCP, memory, subagents, streaming events, and retrieval orchestration.
- Shared packages hold stable contracts and generated clients. They should not contain hidden business logic that makes ownership ambiguous.

## Docker And CI/CD Direction

Each deployable component should have its own Docker image:

- `ghcr.io/<org>/enterprise-search-backend-facade`
- `ghcr.io/<org>/enterprise-search-backend`
- `ghcr.io/<org>/agent-runtime-backend`
- `ghcr.io/<org>/enterprise-search-frontend`

Starting CI/CD model:

- CI on every PR: lint, typecheck, unit tests, builds, and Docker build validation for changed components.
- Path-filtered workflows so unrelated apps/services do not rebuild unnecessarily.
- CD after merge to `main`: build and push service images to GitHub Container Registry.
- Staging deploy from `main`.
- Production deploy through GitHub Environments with manual approval.
- Desktop apps use platform-specific pipelines later: macOS runners for Mac builds, and Windows runners for Windows builds.

## Current Status

The workspace now includes initial scaffolding for `apps/frontend`, `services/backend-facade`, `services/backend`, `services/ai-backend`, and `packages/api-types`.

Start there:

- `services/ai-backend/README.md`
- `services/ai-backend/docs/README.md`
- `docs/architecture/workspace-topology.md`
- `docs/architecture/service-boundaries.md`

## Repo Rules

- Keep service boundaries clear. Do not put frontend, facade, core backend, or native app concerns into `services/ai-backend`.
- Prefer stable APIs and generated clients between components over direct cross-service imports.
- Do not import implementation code across `apps/*` or `services/*`. Cross-component integration must use HTTP APIs, queues/events, or generated contracts from `packages/api-types`.
- Each deployable component owns its dependency environment and Dockerfile:
  - Python services use a service-local `.venv`, `requirements.txt`, and `Dockerfile`.
  - The web frontend uses its own npm workspace environment, `package.json`/`package-lock.json`, and `Dockerfile`.
- Document responsibilities before implementation when introducing a new component.
- Treat permissions, auth context, and tenant boundaries as cross-cutting product requirements.
- Every implementation should include focused unit tests and edge-case coverage appropriate to its component.
- Do not create shared packages just to avoid a small amount of duplication; share only stable contracts and truly cross-cutting primitives.

