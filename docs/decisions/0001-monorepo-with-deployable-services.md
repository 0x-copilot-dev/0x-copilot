# ADR 0001: Monorepo With Deployable Services

## Status

Accepted for initial product development.

## Context

0xCopilot will include web, Mac, Windows, a backend facade, core backend services, and an AI backend. These components will evolve together early in the product. API contracts, auth context, permissions, streaming behavior, and UI flows will change frequently.

At the same time, backend services should have clear runtime boundaries and deploy independently.

## Decision

Use one GitHub monorepo for the 0xCopilot workspace, with independently deployable services inside it.

The target shape is:

- `apps/*` for web and native clients.
- `services/*` for backend services.
- `packages/*` for stable shared contracts and cross-cutting primitives.
- `infra/*` for Docker and local orchestration.
- `docs/*` for architecture, CI/CD, and decisions.

`services/ai-backend` is the canonical AI backend service path.

## Consequences

Benefits:

- One pull request can update an app, service contract, backend implementation, and docs together.
- Path-filtered CI can run only impacted jobs.
- Shared contracts are easier to generate and version.
- Developers learn one GitHub repo, one workflow model, and one local workspace.

Tradeoffs:

- CI must be path-aware to avoid becoming slow.
- Shared packages require discipline to avoid becoming dumping grounds.
- Service boundaries must be enforced through docs, rules, and tests because code is colocated.

## Revisit When

- Separate teams need independent repository access.
- CI remains slow despite path filters and caching.
- A service becomes a standalone product.
- Security boundaries require separate repositories.
- Deployment ownership diverges enough that monorepo coordination hurts more than it helps.
