/**
 * OpenTelemetry browser surface (light side).
 *
 * Only imports `@opentelemetry/api` (~5KB) so the main bundle stays small.
 * `appTracer()` works synchronously at any time — pre-bootstrap calls return
 * a no-op tracer (OTel's default when no provider is registered), which is
 * the right behavior: error boundaries and global error handlers can call
 * it before the SDK has loaded and just produce no spans, not exceptions.
 *
 * The actual SDK (8 heavy packages, ~50KB) is in `otel-sdk.ts` and only
 * loads when `bootstrapTelemetry()` runs — main.tsx defers that to an idle
 * callback after first paint so the SDK init cost stays off the TTI path.
 *
 * Structural enforcement of "no LLM I/O or PII in telemetry" lives in
 * `otel-sdk.ts`'s `SafeAttributeSpanProcessor`: it strips any span
 * attribute whose key is not on the fixed allowlist before export. Even
 * if a future fetch instrumentor tries to capture request/response bodies,
 * those attributes are dropped on the wire.
 */

import { trace } from "@opentelemetry/api";

export const SERVICE_NAME = "enterprise-search-frontend";

export interface BootstrapOptions {
  /**
   * OTLP/HTTP collector endpoint. Defaults to the same-origin facade
   * passthrough at `/v1/telemetry/otlp/v1/traces`. Set to `null` to disable
   * the exporter entirely (spans still produced for tests).
   */
  endpoint?: string | null;
  serviceVersion?: string;
  environment?: string;
}

let bootstrapped = false;

/**
 * Lazy-initialize the OTel SDK. Idempotent; safe to call from an idle
 * callback. Returns a promise that resolves once the SDK chunk is loaded
 * and the provider is registered. Callers that need to know when spans
 * start exporting can `await` it; main.tsx fires and forgets.
 */
export async function bootstrapTelemetry(
  options: BootstrapOptions = {},
): Promise<void> {
  if (bootstrapped) {
    return;
  }
  bootstrapped = true;
  // Dynamic import → Vite emits otel-sdk as its own chunk, off the TTI
  // critical path. The chunk only includes the 8 heavy SDK packages; the
  // tiny @opentelemetry/api surface stays in the main bundle so the
  // no-op tracer keeps working between page load and SDK init.
  const { initOtelSdk } = await import("./otel-sdk");
  initOtelSdk(options);
}

/** Acquire a tracer with the standard service name. */
export function appTracer(): ReturnType<typeof trace.getTracer> {
  return trace.getTracer(SERVICE_NAME);
}
