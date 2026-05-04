/**
 * Global handlers for `unhandledrejection` and `error` events.
 *
 * The browser surfaces a lot of noise from extensions (Grammarly, password
 * managers, Honey, Tampermonkey, …) as if it were app code. The most common
 * one is the message-channel error from `chrome.runtime.onMessage` listeners
 * that return `true` (async response) but then have their port closed before
 * `sendResponse` runs. We classify each error so the noise is obvious in the
 * console and so we don't pollute OTEL with non-app failures.
 *
 * Categories:
 *   - "extension" — stack frame in chrome-extension://, moz-extension://,
 *     or message matches a known extension-only pattern. Logged with
 *     [extension-noise] prefix so it's filterable, NOT exported to OTEL.
 *   - "app"       — anything else. Logged with [app-error] prefix and emits
 *     a single OTEL span (allowlisted attributes only — error.class,
 *     error.kind, route — never message/stack).
 *
 * If you're hunting a real app bug, filter the console for `[app-error]`.
 * If something looks like an app error but is mis-classified, add the
 * matching pattern to `EXTENSION_MESSAGE_PATTERNS` below.
 */

import { SpanStatusCode } from "@opentelemetry/api";

import { appTracer } from "./otel";

export type ErrorCategory = "app" | "extension";
export type ErrorKind = "uncaught" | "unhandled_rejection";

const EXTENSION_URL_PROTOCOLS = [
  "chrome-extension://",
  "moz-extension://",
  "safari-extension://",
  "safari-web-extension://",
  "ms-browser-extension://",
] as const;

// Substrings of error messages that ONLY originate from browser-extension
// runtimes. Keep this list narrow — false positives would silently swallow
// real app errors. The first entry is the canonical Chrome message-channel
// race that prompted this module.
const EXTENSION_MESSAGE_PATTERNS = [
  "A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received",
  "Extension context invalidated",
  "Could not establish connection. Receiving end does not exist",
  "The message port closed before a response was received",
] as const;

interface ClassifiedError {
  category: ErrorCategory;
  errorClass: string;
  reason: "stack-extension-url" | "message-pattern" | "default";
}

export function classifyError(error: unknown): ClassifiedError {
  const errorClass = errorClassNameOf(error);
  const stack = stackOf(error);
  if (stack !== null && hasExtensionFrame(stack)) {
    return { category: "extension", errorClass, reason: "stack-extension-url" };
  }
  const message = messageOf(error);
  if (message !== null && matchesExtensionPattern(message)) {
    return { category: "extension", errorClass, reason: "message-pattern" };
  }
  return { category: "app", errorClass, reason: "default" };
}

export interface InstallOptions {
  /** Override the global object (used in tests). */
  target?: EventTarget;
  /** Override the console sink (used in tests). */
  logger?: Pick<Console, "warn" | "error">;
  /** When true, also log [extension-noise] entries. Default: dev only. */
  logNoise?: boolean;
}

export function installGlobalErrorHandlers(options: InstallOptions = {}): {
  uninstall: () => void;
} {
  const target =
    options.target ?? (typeof window === "undefined" ? null : window);
  if (target === null) {
    // Non-browser env (SSR / vitest jsdom-less). Nothing to install.
    return { uninstall: () => {} };
  }
  const logger = options.logger ?? console;
  const logNoise = options.logNoise ?? import.meta.env?.DEV ?? false;

  const onUnhandledRejection = (event: Event): void => {
    const reason = (event as PromiseRejectionEvent).reason;
    handle({ error: reason, kind: "unhandled_rejection", logger, logNoise });
  };
  const onError = (event: Event): void => {
    const errorEvent = event as ErrorEvent;
    handle({
      error: errorEvent.error ?? errorEvent.message,
      kind: "uncaught",
      logger,
      logNoise,
    });
  };

  target.addEventListener("unhandledrejection", onUnhandledRejection);
  target.addEventListener("error", onError);

  return {
    uninstall: () => {
      target.removeEventListener("unhandledrejection", onUnhandledRejection);
      target.removeEventListener("error", onError);
    },
  };
}

function handle({
  error,
  kind,
  logger,
  logNoise,
}: {
  error: unknown;
  kind: ErrorKind;
  logger: Pick<Console, "warn" | "error">;
  logNoise: boolean;
}): void {
  const classified = classifyError(error);
  if (classified.category === "extension") {
    if (logNoise) {
      logger.warn(
        `[extension-noise] ${kind} suppressed (reason=${classified.reason}, class=${classified.errorClass})`,
        error,
      );
    }
    return;
  }
  // App error: log with the prefix, plus the raw value so devtools shows the
  // stack inline. We deliberately KEEP the raw error in the console (not OTEL)
  // because devtools is local-only and stripping it would break debuggability.
  logger.error(`[app-error] ${kind} (class=${classified.errorClass})`, error);
  emitAppErrorSpan({ kind, errorClass: classified.errorClass });
}

function emitAppErrorSpan({
  kind,
  errorClass,
}: {
  kind: ErrorKind;
  errorClass: string;
}): void {
  // OTEL telemetry follows the existing allowlist contract — only the error
  // class and the kind ever leave the browser. The actual message/stack stay
  // in the local devtools console, never exported.
  try {
    const span = appTracer().startSpan("frontend.global_error");
    span.setAttribute("app.event", "frontend.global_error");
    span.setAttribute("app.error_class", errorClass);
    span.setAttribute("app.error_kind", kind);
    span.setStatus({ code: SpanStatusCode.ERROR });
    span.end();
  } catch {
    // Telemetry is best-effort — never let it shadow the console output.
  }
}

function errorClassNameOf(error: unknown): string {
  if (error instanceof Error) {
    return error.constructor.name;
  }
  if (error === null) {
    return "null";
  }
  return typeof error;
}

function messageOf(error: unknown): string | null {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  if (error !== null && typeof error === "object" && "message" in error) {
    const value = (error as { message: unknown }).message;
    return typeof value === "string" ? value : null;
  }
  return null;
}

function stackOf(error: unknown): string | null {
  if (error instanceof Error && typeof error.stack === "string") {
    return error.stack;
  }
  if (error !== null && typeof error === "object" && "stack" in error) {
    const value = (error as { stack: unknown }).stack;
    return typeof value === "string" ? value : null;
  }
  return null;
}

function hasExtensionFrame(stack: string): boolean {
  return EXTENSION_URL_PROTOCOLS.some((prefix) => stack.includes(prefix));
}

function matchesExtensionPattern(message: string): boolean {
  return EXTENSION_MESSAGE_PATTERNS.some((needle) => message.includes(needle));
}
