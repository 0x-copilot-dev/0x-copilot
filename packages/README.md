# Packages

Shared packages hold stable contracts and cross-cutting primitives. They are not
a place to hide product ownership or avoid small amounts of component-local
duplication.

## Current Packages

- `api-types`: TypeScript contracts for app-facing API payloads and runtime
  event shapes.
- `design-system`: React theme, shared UI primitives, and CSS tokens used by
  the frontend.

## Planned Packages

- `shared-config`: Planned shared lint, formatting, TypeScript, Python, and CI
  configuration package. Do not reference it from builds until it exists.

## Engineering Rules

- Shared packages must not import from `apps/*` or `services/*`.
- Shared packages should expose stable APIs with narrow ownership.
- App-facing contract changes should be documented and tested at the owning
  service boundary.
- UI primitives should stay generic and accessible; app-specific flows belong in
  `apps/frontend`.

See also:

- `../docs/architecture/service-boundaries.md`
- `api-types/README.md`
- `design-system/README.md`
