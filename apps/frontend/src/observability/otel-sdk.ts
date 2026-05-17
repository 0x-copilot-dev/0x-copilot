/**
 * Heavy OpenTelemetry SDK bootstrap — split from otel.ts so the 50+KB of
 * SDK code only lands in the bundle as a separate Vite chunk loaded after
 * first paint. The light `otel.ts` (just @opentelemetry/api) stays in
 * the main bundle so `appTracer()` works synchronously at any time (it
 * returns a no-op tracer until this module initializes the provider).
 *
 * If you're adding new heavy OTel imports, they belong here, not in
 * otel.ts. The substrate-port rule for telemetry is: the API surface is
 * always available; the SDK only loads when the page is settled.
 */

import { context } from "@opentelemetry/api";
import { ZoneContextManager } from "@opentelemetry/context-zone";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  type ReadableSpan,
  type Span,
  type SpanProcessor,
  WebTracerProvider,
} from "@opentelemetry/sdk-trace-web";
import {
  ATTR_SERVICE_NAME,
  ATTR_SERVICE_VERSION,
} from "@opentelemetry/semantic-conventions";
import { dynamicCorrelationHeaders } from "../api/http";
import { SERVICE_NAME, type BootstrapOptions } from "./otel";

/**
 * Allowlist of span attribute keys that may leave the browser.
 *
 * Anything not in this set is stripped by `SafeAttributeSpanProcessor.onEnd`
 * before the span is handed to the OTLP exporter. The set is intentionally
 * small: it lets us debug latency, route, and outcome without ever exporting
 * user-typed text, model output, or auth material.
 */
export const SAFE_ATTRIBUTE_KEYS = new Set<string>([
  // OTEL HTTP semantic conventions (safe subset)
  "http.method",
  "http.request.method",
  "http.status_code",
  "http.response.status_code",
  "http.scheme",
  "http.url.host",
  "http.url.scheme",
  "http.host",
  "server.address",
  "server.port",
  "url.scheme",
  // App-domain attributes (must be added explicitly, never derived from content)
  "app.run_id",
  "app.conversation_id",
  "app.org_id",
  "app.user_id",
  "app.error_class",
  "app.error_code",
  "app.build_sha",
  "app.event",
  "app.route",
  // Resource-shaped attrs the SDK adds; OK to keep
  "service.name",
  "service.version",
  "deployment.environment",
]);

class SafeAttributeSpanProcessor implements SpanProcessor {
  private readonly inner: SpanProcessor;

  constructor(inner: SpanProcessor) {
    this.inner = inner;
  }

  onStart(span: Span, parentContext: ReturnType<typeof context.active>): void {
    this.inner.onStart(span, parentContext);
  }

  onEnd(span: ReadableSpan): void {
    const attrs = span.attributes as Record<string, unknown>;
    for (const key of Object.keys(attrs)) {
      if (!SAFE_ATTRIBUTE_KEYS.has(key)) {
        delete attrs[key];
      }
    }
    this.inner.onEnd(span);
  }

  shutdown(): Promise<void> {
    return this.inner.shutdown();
  }

  forceFlush(): Promise<void> {
    return this.inner.forceFlush();
  }
}

/**
 * Initialize the global tracer provider + instrumentations. Called once
 * by `bootstrapTelemetry()` inside an idle callback after first paint.
 */
export function initOtelSdk(options: BootstrapOptions): void {
  const endpoint =
    options.endpoint === null ? null : (options.endpoint ?? defaultEndpoint());
  const version =
    options.serviceVersion ??
    (typeof __BUILD_SHA__ !== "undefined" ? __BUILD_SHA__ : "dev");
  const env =
    options.environment ??
    (typeof __DEPLOY_ENV__ !== "undefined" ? __DEPLOY_ENV__ : "development");

  const resource = resourceFromAttributes({
    [ATTR_SERVICE_NAME]: SERVICE_NAME,
    [ATTR_SERVICE_VERSION]: version,
    "deployment.environment": env,
  });

  const processors: SpanProcessor[] = [];
  if (endpoint !== null) {
    const exporter = new OTLPTraceExporter({
      url: endpoint,
      headers: dynamicCorrelationHeaders(),
    });
    processors.push(
      new SafeAttributeSpanProcessor(new BatchSpanProcessor(exporter)),
    );
  }

  const provider = new WebTracerProvider({
    resource,
    spanProcessors: processors,
  });
  provider.register({ contextManager: new ZoneContextManager() });

  registerInstrumentations({
    instrumentations: [
      new DocumentLoadInstrumentation(),
      // Body capture is disabled by default; we still pass empty hooks to make
      // the intent obvious to anyone reading this code.
      new FetchInstrumentation({
        clearTimingResources: true,
        propagateTraceHeaderCorsUrls: [/.*/],
      }),
    ],
  });
}

/**
 * Build the default OTLP endpoint as an absolute URL.
 *
 * The OTLP/HTTP exporter requires an absolute URL (it parses with `new URL`
 * without a base). In a normal browser environment we pin it to the same
 * origin as the page so traces flow through the facade passthrough; in
 * non-browser environments (vitest jsdom without `window.location`, SSR)
 * we return `null` and the SDK runs without an exporter.
 */
function defaultEndpoint(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const origin = window.location?.origin;
  if (!origin || origin === "null") {
    return null;
  }
  return `${origin}/v1/telemetry/otlp/v1/traces`;
}
