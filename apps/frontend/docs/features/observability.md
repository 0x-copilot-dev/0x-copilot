# Observability

OTEL spans + a global error classifier. Structural enforcement of "no LLM
I/O, no user content, no auth material in telemetry."

See also:

- [../architecture/01-network-layer.md](../architecture/01-network-layer.md) —
  the OTLP exporter rides `dynamicCorrelationHeaders()`

Source: [`src/observability/otel.ts`](../../src/observability/otel.ts),
[`src/observability/globalErrorHandlers.ts`](../../src/observability/globalErrorHandlers.ts),
[`src/observability/ErrorBoundary.tsx`](../../src/observability/ErrorBoundary.tsx)

---

## OTEL bootstrap

`bootstrapTelemetry()` runs once from `src/main.tsx` and:

1. Builds a `Resource` with `service.name=0x-copilot-frontend`,
   `service.version=__BUILD_SHA__`, `deployment.environment=__DEPLOY_ENV__`.
2. Wraps a `BatchSpanProcessor` in a `SafeAttributeSpanProcessor` (see below).
3. Registers `DocumentLoadInstrumentation` and `FetchInstrumentation`
   (body capture explicitly disabled — even a hypothetical future fetch
   instrumentor that captures bodies would be stripped at export).
4. Exports to **`/v1/telemetry/otlp/v1/traces`** — same-origin, passes
   through the facade. The browser never talks to the OTEL collector
   directly so the collector stays inside the customer perimeter.

`endpoint: null` disables the exporter (still produces spans for tests).

---

## Safe-attribute allowlist

`SafeAttributeSpanProcessor.onEnd` deletes every span attribute whose key
is not in the allowlist before handing the span to the exporter.

```ts
const SAFE_ATTRIBUTE_KEYS = new Set([
  // OTEL HTTP semconv (safe subset)
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
  // App-domain (must be added explicitly, never derived from content)
  "app.run_id",
  "app.conversation_id",
  "app.org_id",
  "app.user_id",
  "app.error_class",
  "app.error_code",
  "app.build_sha",
  "app.event",
  "app.route",
  // Resource-shaped (SDK adds these; OK to keep)
  "service.name",
  "service.version",
  "deployment.environment",
]);
```

Adding a new attribute key requires editing the allowlist **and** thinking
about whether the value can carry user content (prompt text, model output,
PII). When in doubt: don't add it.

---

## Error reporting from `<ErrorBoundary>`

A React error caught by `<ErrorBoundary>` emits a single span with the
shape:

```
name:   frontend.error_boundary
attrs:  app.event=frontend.error_boundary,
        app.error_class=<constructor name>,
        app.route=<URL template>
status: ERROR
```

Never `error.message`, never `error.stack`, never the React
`componentStack` — both can carry user-typed content via React's error
formatting.

The boundary keeps the original error in `console.error` so devtools still
shows the full stack inline; that channel is local to the browser and
never exported.

---

## Global error handlers

`installGlobalErrorHandlers()` registers handlers for `unhandledrejection`
and `error` events on the window. Every event is classified:

| Category    | When                                                                                                                  | Action                                                                                                                    |
| ----------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `extension` | Stack frame from `chrome-extension://` / `moz-extension://` (etc.), or message matches a known extension-only pattern | Console: `[extension-noise] …` (dev only by default). NOT exported to OTEL.                                               |
| `app`       | Anything else                                                                                                         | Console: `[app-error] …` + raw error for devtools stack. OTEL: one `frontend.global_error` span (allowlisted attrs only). |

`EXTENSION_MESSAGE_PATTERNS` is intentionally narrow — false positives
would silently swallow real app errors. The first entry is the canonical
Chrome `chrome.runtime.onMessage` race that prompted this module:

> "A listener indicated an asynchronous response by returning true, but the
> message channel closed before a response was received"

If something looks like an app error but is mis-classified, add the
matching pattern to `EXTENSION_MESSAGE_PATTERNS` (in
`globalErrorHandlers.ts`) with a one-line comment on which extension
emitted it.

---

## Debugging tips

- Filter the console for **`[app-error]`** to see only real app failures.
- Filter for **`[extension-noise]`** (dev only) to confirm an extension is
  the source.
- Filter for **`[vite-proxy]`** (dev only) to see every `/v1/*` request and
  its upstream status code (the proxy logs are wired in `vite.config.ts`).
