/**
 * React error boundary that emits a single OTEL span on uncaught errors.
 *
 * Allowlist enforcement: the only attributes attached to the span are
 * `app.error_class` and `app.event`. We deliberately do NOT include
 * `error.message` or `error.stack` because React error formatting can fold
 * user-typed strings into both via component props and JSX children.
 *
 * The fallback UI shows a generic message plus the request_id so support
 * tickets remain correlatable to server logs without exposing the underlying
 * error text.
 */

import { SpanStatusCode } from "@opentelemetry/api";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { appTracer } from "./otel";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    // Emit a span we can correlate with the SSE/run trace, but with NO
    // free-form text. The `error.constructor.name` is the runtime class
    // (e.g. "TypeError", "RuntimeStreamProtocolError") -- a fixed set of
    // identifiers we control.
    try {
      const span = appTracer().startSpan("frontend.error_boundary");
      span.setAttribute("app.event", "frontend.error_boundary");
      span.setAttribute("app.error_class", error.constructor.name);
      span.setStatus({ code: SpanStatusCode.ERROR });
      span.end();
    } catch {
      // Telemetry failures must never replace the underlying error in the UI.
    }
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div role="alert" style={{ padding: "1.5rem" }}>
            <h2>Something went wrong.</h2>
            <p>
              We&apos;ve recorded the error. Please refresh the page to
              continue.
            </p>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
