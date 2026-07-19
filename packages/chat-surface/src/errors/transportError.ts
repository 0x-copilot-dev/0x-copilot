// transportError — one place that turns a rejected Transport/IPC request into
// the structured, user-actionable parts of the backend error envelope.
//
// Why this exists: the facade returns `{ detail: { code, safe_message,
// correlation_id, retryable, details } }` (a direct ai-backend caller gets the
// same object flat, without the `detail` wrapper). Both the web `WebTransport`
// and the desktop main process throw an `Error` whose `.message` is the raw
// stringified body, and on desktop Electron additionally prefixes it with
// `"Error invoking remote method 'transport.request': Error: "`. So the
// actionable line (`safe_message`) and the branch key (`code`) survive only as
// substrings of `err.message`.
//
// `parseTransportError` recovers them by slicing the JSON substring out of the
// message (which also strips the Electron prefix), tolerating BOTH the
// facade-wrapped `{ detail: {...} }` shape and the flat `{ code, ... }` shape.
// Callers surface `safeMessage` (never the raw envelope), branch on `code`
// (e.g. `configuration_error` → an "Add a provider key" CTA), and demote
// `correlationId` / `raw` behind a "Show details" affordance.
//
// Boundary: pure — no window/fetch/DOM. Safe in the substrate-agnostic package.

/** The structured, user-facing parts of a transport/IPC error. */
export interface ParsedTransportError {
  /**
   * The actionable server line — the facade `safe_message` (or a string
   * `detail`). Undefined when the message carried no parseable envelope.
   */
  readonly safeMessage?: string;
  /** Machine code for branching, e.g. `"configuration_error"`. */
  readonly code?: string;
  /** Support correlation id — demote behind a details/copy affordance. */
  readonly correlationId?: string;
  /** The original message text, for a copyable "Show details" fallback. */
  readonly raw: string;
}

/**
 * Best-effort structured parse of a rejected Transport/IPC request. Never
 * throws; returns `{ raw }` (with `safeMessage`/`code`/`correlationId`
 * populated when the envelope is present).
 */
export function parseTransportError(err: unknown): ParsedTransportError {
  const raw =
    err instanceof Error ? err.message : typeof err === "string" ? err : "";
  if (raw === "") {
    return { raw };
  }

  // Slice the JSON body out of the (possibly Electron-prefixed) message rather
  // than JSON.parse-ing the whole string.
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start < 0 || end <= start) {
    return { raw };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.slice(start, end + 1));
  } catch {
    return { raw };
  }

  // The facade wraps the ai-backend body under `detail`; a direct ai-backend
  // caller gets it flat. Descend into `detail` when present, else use the
  // object itself.
  const detail =
    parsed !== null && typeof parsed === "object" && "detail" in parsed
      ? (parsed as { detail: unknown }).detail
      : parsed;

  // A plain string `detail` (`{ detail: "not found" }`) is itself the message.
  if (typeof detail === "string") {
    return detail === "" ? { raw } : { safeMessage: detail, raw };
  }

  if (detail === null || typeof detail !== "object") {
    return { raw };
  }

  const obj = detail as Record<string, unknown>;
  const safeMessage =
    typeof obj.safe_message === "string" && obj.safe_message !== ""
      ? obj.safe_message
      : undefined;
  const code =
    typeof obj.code === "string" && obj.code !== "" ? obj.code : undefined;
  const correlationId =
    typeof obj.correlation_id === "string" && obj.correlation_id !== ""
      ? obj.correlation_id
      : undefined;

  return { safeMessage, code, correlationId, raw };
}

/**
 * A user-facing one-liner for a transport/IPC failure that has NO structured
 * envelope (e.g. a dropped SSE stream, a network error). Prefers the server
 * `safeMessage` when present; otherwise strips the desktop Electron wrapper
 * (`Error invoking remote method 'transport.request': Error: …`) and any bare
 * `Error:` prefix, and falls back to a generic line if what remains still names
 * an internal method — so a remote-method identifier is NEVER surfaced (NFR-2.1).
 */
export function humanTransportMessage(err: unknown): string {
  const parsed = parseTransportError(err);
  if (parsed.safeMessage !== undefined) {
    return parsed.safeMessage;
  }
  const stripped = parsed.raw
    .replace(/Error invoking remote method '[^']*':\s*/gi, "")
    .replace(/^Error:\s*/i, "")
    .trim();
  if (stripped === "" || /remote method|transport\.request/i.test(stripped)) {
    return "The connection was interrupted.";
  }
  return stripped;
}
