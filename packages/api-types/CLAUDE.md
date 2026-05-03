# API Types

TypeScript contracts for app-facing payloads. Consumed by `apps/*`. Mirrors public server contracts from `services/backend` and `services/ai-backend`.

## Before changing contracts

Read `packages/api-types/README.md` and `SPEC.md` first.

## What belongs here

Type-only mirrors of the **public** HTTP surface served via `backend-facade`:

- Request / response shapes
- Enum values
- SSE event envelope shapes

## What does NOT belong here

- Business logic
- `fetch` / HTTP wrappers (those live in `apps/frontend/src/api/*`)
- Route ownership (servers own routes; this package only describes the payloads)
- UI view models (apps shape view models from these contracts)
- `/internal/v1/*` shapes — internal contracts are not mirrored here

## Breaking changes

Treat as breaking and flag with a migration note in the PR:

- Removing an enum value
- Adding a required field
- Removing a response field
- Changing a field's type
- Renaming a field

Optional additions and new enum values **on a payload the server already tolerates** are non-breaking.

## Source of truth

Server is the source of truth — types here mirror what the server actually serves. When you change a server payload, update this package in the same change.

## Validation

```bash
npm run typecheck --workspace @enterprise-search/api-types
```

Also run frontend typecheck when this package's changes affect consumers.
