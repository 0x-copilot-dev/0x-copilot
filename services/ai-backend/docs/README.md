# AI Backend Docs

This directory documents the current enterprise AI backend runtime. Completed implementation PRDs have been removed; the technical specs, architecture docs, testing guidance, and rules are now the source of truth for implemented runtime work. New PRDs may be added only for future product or architecture work before implementation begins.

## Workspace Context

`services/ai-backend` is the canonical path for the AI backend service inside the `enterprise-search` monorepo.

Before changing runtime APIs, service contracts, streaming surfaces, or ownership boundaries, read the workspace-level docs:

- `../../../docs/architecture/workspace-topology.md`
- `../../../docs/architecture/service-boundaries.md`
- `../../../docs/ci-cd/github-actions-strategy.md`
- `../../../docs/decisions/0001-monorepo-with-deployable-services.md`

The AI backend exposes agent runtime contracts to `backend-facade`; frontend and native apps should not call it directly unless a future accepted spec creates a narrow exception.

The FastAPI runtime API remains limited to conversations, runs, event replay, streaming, cancellation, and approvals. Now that `backend-facade` exists, apps should reach those routes through the facade. The exception must not expand into tenant auth, billing/admin state, or non-agent product persistence.

## Read Order

1. Workspace topology and service boundaries in `../../../docs/architecture/`
2. `architecture/system-overview.md`
3. `architecture/data-flow.md`
4. The matching technical spec in `specs/`
5. `testing/unit-testing-strategy.md` and `testing/edge-case-matrix.md`
6. The relevant rule docs in `rules/`

## Feature Map

| Feature                          | Current docs                                                           | Spec                                                |
| -------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------- |
| Product vision                   | `prds/00-product-vision.md`, `architecture/system-overview.md`         | `architecture/runtime-contracts.md`                 |
| Runtime foundation               | `architecture/system-overview.md`, `architecture/package-structure.md` | `specs/01-runtime-foundation-spec.md`               |
| Dynamic tool loading             | `architecture/data-flow.md`                                            | `specs/02-dynamic-tool-loading-spec.md`             |
| Skills middleware                | `architecture/data-flow.md`                                            | `specs/03-skills-middleware-spec.md`                |
| Dynamic MCP loading              | `architecture/data-flow.md`                                            | `specs/04-dynamic-mcp-loading-spec.md`              |
| Context and memory               | `architecture/data-flow.md`, `architecture/runtime-contracts.md`       | `specs/05-context-memory-management-spec.md`        |
| Subagents and async agents       | `architecture/data-flow.md`, `architecture/runtime-contracts.md`       | `specs/06-subagents-and-async-agents-spec.md`       |
| Streaming and observability      | `architecture/data-flow.md`, `architecture/runtime-contracts.md`       | `specs/07-streaming-and-observability-spec.md`      |
| FastAPI runtime API              | `architecture/data-flow.md`, `architecture/package-structure.md`       | `specs/08-fastapi-runtime-api-spec.md`              |
| Runtime events producer/consumer | `architecture/data-flow.md`, `architecture/runtime-contracts.md`       | `specs/09-runtime-events-producer-consumer-spec.md` |
| Agent runtime persistence        | `architecture/data-flow.md`, `architecture/runtime-contracts.md`       | `specs/10-agent-runtime-persistence-spec.md`        |

## Definition of Ready For New Runtime Work

A future feature is ready for implementation when its architecture note or spec includes:

- User problem, goals, non-goals, and acceptance criteria.
- Architecture boundaries and module ownership.
- Pydantic contracts for all IO and domain state boundaries.
- Unit test requirements and edge-case cases.
- Security, permission, and failure-mode expectations.

## Definition of Done

An implementation PR must include focused unit tests for the contract, registry, middleware, and failure paths it touches. It must prove edge-case handling with deterministic fake tools, fake MCP servers, fake stores, fake model builders, fake stream chunks, and fake subagent runners rather than relying on external services.
