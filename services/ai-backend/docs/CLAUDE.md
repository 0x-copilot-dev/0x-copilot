# AI Backend Docs — Spec-First Workflow

Docs are the contract future agents implement against. Treat them as authoritative.

## PRD requirements

A PRD must state:

- Problem
- Goals
- Non-goals
- Acceptance criteria
- Risks
- Unit testing requirements

## Spec requirements

A spec must include:

- Architecture
- Module boundaries
- Pydantic contracts (full field-level shape)
- Edge cases
- Security considerations
- Observability (events, metrics, logs)
- Tests

## Workflow

- Before implementing or changing behavior, read the matching spec under `docs/specs/`.
- Keep implementation decisions consistent with Deep Agents, LangGraph, LangChain, and Agent Skills primitives.
- **Do not remove edge cases to simplify implementation.** If an edge case in the spec is hard, raise it — don't drop it silently.
- When implementation changes a contract, invariant, or edge case, update the spec in the same change.

## What docs are not

PRDs are for future product/architecture work that hasn't shipped yet. For day-to-day implementation, the spec is what you read — not the PRD.
