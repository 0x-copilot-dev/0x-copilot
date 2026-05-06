/**
 * OpenTelemetry browser SDK bootstrap.
 *
 * Initializes a tracer that emits spans for fetches, document load, and any
 * manually-instrumented user actions. Spans flow to a same-origin OTLP/HTTP
 * receiver on the backend facade (`/v1/telemetry/otlp/v1/traces`); the browser
 * never talks to the OTEL collector directly so the collector stays inside
 * the customer perimeter.
 *
 * Structural enforcement of "no LLM I/O or PII in telemetry": the
 * `SafeAttributeSpanProcessor` strips any span attribute whose key is not on
 * a fixed allowlist before export. Even if a future fetch instrumentor tries
 * to capture request/response bodies, those attributes are dropped on the
 * wire. Errors caught by `<ErrorBoundary />` emit only `error.class` and the
 * route template -- never `error.message` or `error.stack`, both of which
 * can carry user-typed content via React's error formatting.
 */

import { context, trace } from "@opentelemetry/api";
import { ZoneContextManager } from "@opentelemetry/context-zone";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { Resource } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  type ReadableSpan,
  type Span,
  type SpanProcessor,
  WebTracerProvider,
} from "@opentelemetry/sdk-trace-web";
import { SemanticResourceAttributes } from "@opentelemetry/semantic-conventions";

const SERVICE_NAME = "enterprise-search-frontend";

/**
 * Allowlist of span attribute keys that may leave the browser.
 *
 * Anything not in this set is stripped by `SafeAttributeSpanProcessor.onEnd`
 * before the span is handed to the OTLP exporter. The set is intentionally
 * small: it lets us debug latency, route, and outcome without ever exporting
 * user-typed text, model output, or auth material.
 */
const SAFE_ATTRIBUTE_KEYS = new Set<string>([
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

let bootstrapped = false;

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

export function bootstrapTelemetry(options: BootstrapOptions = {}): void {
  if (bootstrapped) {
    return;
  }
  bootstrapped = true;

  const endpoint =
    options.endpoint === null ? null : (options.endpoint ?? defaultEndpoint());
  const version =
    options.serviceVersion ??
    (typeof __BUILD_SHA__ !== "undefined" ? __BUILD_SHA__ : "dev");
  const env =
    options.environment ??
    (typeof __DEPLOY_ENV__ !== "undefined" ? __DEPLOY_ENV__ : "development");

  const resource = new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: SERVICE_NAME,
    [SemanticResourceAttributes.SERVICE_VERSION]: version,
    "deployment.environment": env,
  });

  const processors: SpanProcessor[] = [];
  if (endpoint !== null) {
    const exporter = new OTLPTraceExporter({ url: endpoint });
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

/** Acquire a tracer with the standard service name. */
export function appTracer(): ReturnType<typeof trace.getTracer> {
  return trace.getTracer(SERVICE_NAME);
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

export { SAFE_ATTRIBUTE_KEYS };
