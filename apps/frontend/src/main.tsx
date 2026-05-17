import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import { ErrorBoundary } from "./observability/ErrorBoundary";
import { installGlobalErrorHandlers } from "./observability/globalErrorHandlers";
import { bootstrapTelemetry } from "./observability/otel";

installGlobalErrorHandlers();

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element was not found");
}

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);

// Defer OTel SDK bootstrap to an idle callback so the 8-package SDK chunk
// loads after first paint, not during it. `appTracer()` calls between page
// load and bootstrap return a no-op tracer (OTel's default with no provider
// registered) — error boundaries and global handlers stay safe to call.
// Falls back to setTimeout on browsers without requestIdleCallback (Safari).
const scheduleIdle =
  typeof window !== "undefined" && "requestIdleCallback" in window
    ? (cb: () => void) => window.requestIdleCallback(cb, { timeout: 2000 })
    : (cb: () => void) => window.setTimeout(cb, 0);
scheduleIdle(() => {
  void bootstrapTelemetry();
});
