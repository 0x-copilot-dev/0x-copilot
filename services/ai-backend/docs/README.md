# AI Backend Docs

This directory documents the current enterprise AI backend runtime. The original seven implementation PRDs have been completed and removed; the technical specs, architecture docs, testing guidance, and rules are now the source of truth for implemented runtime work. New PRDs may be added for future API, persistence, and deployment phases before implementation begins.

## Workspace Context

`services/ai-backend` is the canonical path for the AI backend service inside the `enterprise-search` monorepo.

Before changing runtime APIs, service contracts, streaming surfaces, or ownership boundaries, read the workspace-level docs:

- `../../../docs/architecture/workspace-topology.md`
- `../../../docs/architecture/service-boundaries.md`
- `../../../docs/ci-cd/github-actions-strategy.md`
- `../../../docs/decisions/0001-monorepo-with-deployable-services.md`

The AI backend should expose contracts to `backend-facade`; frontend and native apps should not call it directly unless a future accepted spec creates a narrow exception.

The FastAPI runtime API docs are that narrow exception for the first frontend-facing runtime surface. They allow `services/ai-backend` to expose conversation, run, event, streaming, cancellation, and approval endpoints while `backend-facade` does not exist. The exception must not expand into tenant auth, billing/admin state, or non-agent product persistence.

## Read Order

1. Workspace topology and service boundaries in `../../../docs/architecture/`
2. `architecture/system-overview.md`
3. `architecture/data-flow.md`
4. The matching PRD in `prds/`, when planning future work
5. The matching technical spec in `specs/`
6. `testing/unit-testing-strategy.md` and `testing/edge-case-matrix.md`
7. The relevant rule docs in `rules/`

## Feature Map

| Feature | Current docs | Spec |
| --- | --- | --- |
| Product vision | `prds/00-product-vision.md`, `architecture/system-overview.md` | `architecture/runtime-contracts.md` |
| Runtime foundation | `architecture/system-overview.md`, `architecture/package-structure.md` | `specs/01-runtime-foundation-spec.md` |
| Dynamic tool loading | `architecture/data-flow.md` | `specs/02-dynamic-tool-loading-spec.md` |
| Skills middleware | `architecture/data-flow.md` | `specs/03-skills-middleware-spec.md` |
| Dynamic MCP loading | `architecture/data-flow.md` | `specs/04-dynamic-mcp-loading-spec.md` |
| Context and memory | `architecture/data-flow.md`, `architecture/runtime-contracts.md` | `specs/05-context-memory-management-spec.md` |
| Subagents and async agents | `architecture/data-flow.md`, `architecture/runtime-contracts.md` | `specs/06-subagents-and-async-agents-spec.md` |
| Streaming and observability | `architecture/data-flow.md`, `architecture/runtime-contracts.md` | `specs/07-streaming-and-observability-spec.md` |
| FastAPI runtime API | `prds/01-fastapi-runtime-api-prd.md`, `prds/02-runtime-events-producer-consumer-prd.md` | `specs/04-runtime-api-technical-spec.md` |
| Agent runtime persistence | `prds/03-agent-runtime-persistence-prd.md` | `specs/04-runtime-api-technical-spec.md` |

## Definition of Ready For New Runtime Work

A future feature is ready for implementation when its architecture note or spec includes:

- User problem, goals, non-goals, and acceptance criteria.
- Architecture boundaries and module ownership.
- Pydantic contracts for all IO and domain state boundaries.
- Unit test requirements and edge-case cases.
- Security, permission, and failure-mode expectations.

## Definition of Done

An implementation PR must include focused unit tests for the contract, registry, middleware, and failure paths it touches. It must prove edge-case handling with deterministic fake tools, fake MCP servers, fake stores, fake model builders, fake stream chunks, and fake subagent runners rather than relying on external services.

