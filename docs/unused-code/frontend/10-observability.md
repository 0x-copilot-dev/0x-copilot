# Cluster: Observability (OpenTelemetry + errors)

**Path:** `apps/frontend/src/observability/`  
**Last reviewed:** 2026-05-06

## Scope

- [`otel.ts`](../../../apps/frontend/src/observability/otel.ts) — browser tracing bootstrap, fetch instrumentation hooks.
- [`globalErrorHandlers.ts`](../../../apps/frontend/src/observability/globalErrorHandlers.ts) — `window.error` / unhandled rejection plumbing.
- [`ErrorBoundary.tsx`](../../../apps/frontend/src/observability/ErrorBoundary.tsx) — React error boundary.

## Unused / candidate dead code

_**RESOLVED at `a78bfc0`.**_ `_resetForTests` was removed from `otel.ts`; no test ever imported it. Future observability tests can re-introduce a module-private flag-flip if needed.

## ts-prune signals

| Symbol                                         | Notes                                                          |
| ---------------------------------------------- | -------------------------------------------------------------- |
| `ErrorCategory`, `ErrorKind`, `InstallOptions` | `globalErrorHandlers.ts` — typing / options for installer API. |
| `BootstrapOptions`, `SAFE_ATTRIBUTE_KEYS`      | `otel.ts` — configuration surfaces.                            |

## Smells

- **OTel attribute allowlists** — `SAFE_ATTRIBUTE_KEYS` exists to reduce PII leakage; changes should stay paired with privacy review (workspace compliance expectations).

## Confidence

**High** at the audited revision; the unused `_resetForTests` was removed at `a78bfc0`.
