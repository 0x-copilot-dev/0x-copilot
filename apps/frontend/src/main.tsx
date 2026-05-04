import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import { ErrorBoundary } from "./observability/ErrorBoundary";
import { installGlobalErrorHandlers } from "./observability/globalErrorHandlers";
import { bootstrapTelemetry } from "./observability/otel";

bootstrapTelemetry();
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
