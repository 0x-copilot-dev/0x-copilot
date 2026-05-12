# AI Backend Docs

This KB is the authoritative reference for the `ai-backend` service.

## Before changing behavior

Read [README.md](README.md) to find the relevant doc, then read it before implementing.
Architecture, features, guides, and reference docs are the source of truth.

## When a spec is needed

For new features or significant behavior changes: write a spec first.

A spec must include:

- Module boundaries and file paths
- Pydantic contracts (full field-level shape)
- Edge cases
- Security considerations
- Observability (events, metrics, logs)
- Tests

## Workflow

1. Read [README.md](README.md) — find which doc covers the area you're changing.
2. Read that doc — understand current behavior and invariants.
3. Check [architecture/04-security-invariants.md](architecture/04-security-invariants.md)
   — verify your change does not violate a listed invariant.
4. Implement.
5. Write tests per [guides/testing.md](guides/testing.md).
6. Update the KB doc if you changed a contract, invariant, or module boundary.

## Rules

- **Do not remove edge cases to simplify implementation.** If an edge case is hard, raise it.
- **Never bypass permission checks** in `capabilities/` middleware.
- **Never leak internal errors** to model output or HTTP responses — convert to typed domain errors.
- **Update the KB doc** when implementation changes a contract, invariant, or edge case.
