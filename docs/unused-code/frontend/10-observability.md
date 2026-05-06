# Cluster: Observability (OpenTelemetry + errors)

**Path:** `apps/frontend/src/observability/`  
**Last reviewed:** 2026-05-06

## Scope

- [`otel.ts`](../../../apps/frontend/src/observability/otel.ts) — browser tracing bootstrap, fetch instrumentation hooks.
- [`globalErrorHandlers.ts`](../../../apps/frontend/src/observability/globalErrorHandlers.ts) — `window.error` / unhandled rejection plumbing.
- [`ErrorBoundary.tsx`](../../../apps/frontend/src/observability/ErrorBoundary.tsx) — React error boundary.

## Unused / candidate dead code

| Symbol                                                               | File      | Assessment                                                                                                                                                                                                               |
| -------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`_resetForTests`](../../../apps/frontend/src/observability/otel.ts) | `otel.ts` | Exported for test isolation but **no test file imports it** at this revision (ripgrep). Either **add** observability tests that call `_resetForTests`, **delete** the export, or **rename** to module-private if unused. |

## ts-prune signals

| Symbol                                         | Notes                                                          |
| ---------------------------------------------- | -------------------------------------------------------------- |
| `ErrorCategory`, `ErrorKind`, `InstallOptions` | `globalErrorHandlers.ts` — typing / options for installer API. |
| `BootstrapOptions`, `SAFE_ATTRIBUTE_KEYS`      | `otel.ts` — configuration surfaces.                            |

## Smells

- **Leading underscore export** — `_resetForTests` signals test-only intent but lacks consumers — classic merge-gap smell.
- **OTel attribute allowlists** — `SAFE_ATTRIBUTE_KEYS` exists to reduce PII leakage; changes should stay paired with privacy review (workspace compliance expectations).

## Confidence

**High** that `_resetForTests` is currently unused; **medium** that it was added preemptively for future tests.
