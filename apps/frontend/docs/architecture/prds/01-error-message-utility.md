# PRD: Unified `errorMessage` utility

**Status:** Draft → In implementation
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §5](../05-dry-audit.md)

## Problem

The pattern `err instanceof Error ? err.message : "fallback"` appears
**89 times** across the frontend, plus 5 named helpers
(`toMessage` ×3, `errorMessage` ×2) with identical bodies. Every new
async handler reinvents it. There is no single place that controls
how unknown errors are surfaced to the UI.

## Goals

1. Single canonical helper, importable from one path.
2. Migrate every existing call site (named helpers + inline cases).
3. No behaviour change. Same string output for every input.
4. The helper is the only thing components should reach for when
   converting `unknown` → user-visible message.

## Non-goals

- Reworking how errors propagate from the transport (that's the
  `assertOk` + `UnauthorizedError` path — out of scope).
- Telemetry / logging of errors (separate concern).
- i18n of fallback strings (out of scope until i18n lands).

## Design

```ts
// apps/frontend/src/utils/errors.ts

/**
 * Convert an unknown thrown value into a user-visible message.
 *
 * Trims whitespace. Returns `fallback` if the value is not an Error
 * or its `.message` is empty after trimming. Never returns "".
 *
 * This is the single way frontend code converts `catch (err: unknown)`
 * into a string that can land in `setError(...)` or be rendered.
 * Do not roll a local copy in feature code.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) {
    const msg = err.message?.trim();
    if (msg) return msg;
  }
  return fallback;
}
```

### Why a free function, not a class / Result type

- The codebase already throws plain `Error` (and `UnauthorizedError`
  which extends it). A wrapper type would force a second migration.
- Components consume the string directly into `useState<string|null>`.
  No need for a richer type than `string`.
- Trim is added so `new Error("  ")` doesn't pass the truthiness check
  and leave an empty banner.

### Why `fallback` is required, not defaulted

Forcing every caller to supply a fallback keeps user-facing copy at
the call site, which is where the domain context lives ("Could not
load catalog" vs "Could not save policy"). A default ("Something went
wrong") would erase that context.

## Migration

1. **Create `apps/frontend/src/utils/errors.ts`** with the function above
   and a brief unit test.
2. **Delete the 5 named helpers** (`toMessage` × 3, `errorMessage` × 2);
   replace their usages with the import.
3. **Codemod the 89 inline cases** to `errorMessage(err, "...")`. The
   inline pattern is regular enough that this is safe to do by
   `sed`-and-review, but we'll do it file-by-file in two batches.

   Pattern (regex form):
   `err instanceof Error \? err\.message : ("[^"]*")`
   → `errorMessage(err, $1)`

4. **Add a lint rule (optional, follow-up):** disallow
   `err instanceof Error ? err.message :` outside `utils/errors.ts`.

## Validation

- `npm run typecheck --workspace @enterprise-search/frontend`
- `npm run build --workspace @enterprise-search/frontend`
- `cd apps/frontend && npx vitest run` (jsdom/component tests)
- Spot-check 5 randomly chosen call sites in the browser to confirm
  the same string surfaces.

## Risks

- A few inline cases use a non-string fallback expression (computed at
  call time). The codemod must preserve those — manual review on any
  match whose `fallback` capture isn't a plain string literal.
- Some call sites use `err.message ?? fallback` instead of
  `instanceof Error ?` — the new helper's `trim` is stricter
  (whitespace-only messages now fall back). This is an intentional
  improvement; flag any test that depends on whitespace messages.

## Rollback

Single-file revert + 5 helper restorations. No schema, no API, no
storage involved.
