# AI Backend Docs

This directory is the implementation handoff for the enterprise AI backend. It is intentionally documentation-first: agents should not write feature code until they have read the relevant PRD, technical spec, testing guidance, and rules.

## Workspace Context

`ai-backend-src` is the current transitional path for the AI backend service. The future canonical location is expected to be `services/ai-backend` inside the `enterprise-search` monorepo.

Before changing runtime APIs, service contracts, streaming surfaces, or ownership boundaries, read the workspace-level docs:

- `../../docs/architecture/workspace-topology.md`
- `../../docs/architecture/service-boundaries.md`
- `../../docs/ci-cd/github-actions-strategy.md`
- `../../docs/decisions/0001-monorepo-with-deployable-services.md`

The AI backend should expose contracts to `backend-facade`; frontend and native apps should not call it directly unless a future accepted spec creates a narrow exception.

## Read Order

1. Workspace topology and service boundaries in `../../docs/architecture/`
2. `architecture/system-overview.md`
3. The relevant PRD in `prds/`
4. The matching technical spec in `specs/`
5. `testing/unit-testing-strategy.md` and `testing/edge-case-matrix.md`
6. The relevant rule docs in `rules/`

## Feature Map

| Feature | PRD | Spec |
| --- | --- | --- |
| Product vision | `prds/00-product-vision.md` | `architecture/system-overview.md` |
| Runtime foundation | `prds/01-runtime-foundation.md` | `specs/01-runtime-foundation-spec.md` |
| Dynamic tool loading | `prds/02-dynamic-tool-loading.md` | `specs/02-dynamic-tool-loading-spec.md` |
| Skills middleware | `prds/03-skills-middleware.md` | `specs/03-skills-middleware-spec.md` |
| Dynamic MCP loading | `prds/04-dynamic-mcp-loading.md` | `specs/04-dynamic-mcp-loading-spec.md` |
| Context and memory | `prds/05-context-memory-management.md` | `specs/05-context-memory-management-spec.md` |
| Subagents and async agents | `prds/06-subagents-and-async-agents.md` | `specs/06-subagents-and-async-agents-spec.md` |
| Streaming and observability | `prds/07-streaming-and-observability.md` | `specs/07-streaming-and-observability-spec.md` |

## Definition of Ready

A feature is ready for implementation when its PRD and spec include:

- User problem, goals, non-goals, and acceptance criteria.
- Architecture boundaries and module ownership.
- Pydantic contracts for all IO and domain state boundaries.
- Unit test requirements and edge-case cases.
- Security, permission, and failure-mode expectations.

## Definition of Done

An implementation PR must include focused unit tests for the contract, registry, middleware, and failure paths it touches. It must prove edge-case handling with deterministic fake tools, fake MCP servers, fake stores, and fake subagent runners rather than relying on external services.

