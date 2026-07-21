export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export type QueryParamValue = string | number | boolean | undefined;

export interface TypedRequest {
  readonly method: HttpMethod;
  readonly path: string;
  readonly query?: Readonly<Record<string, QueryParamValue>>;
  readonly body?: unknown;
  readonly headers?: Readonly<Record<string, string>>;
  readonly signal?: AbortSignal;
}

export interface Session {
  readonly bearer: string | null;
}

export interface TransportCapabilities {
  readonly substrate: "web" | "desktop-webview";
  readonly nativeSecretStorage: boolean;
  readonly fileSystemAccess: boolean;
  readonly clipboardWrite: boolean;
  readonly openExternal: boolean;
}

export interface SseSubscribeOptions {
  readonly path: string;
  readonly query?: Readonly<Record<string, QueryParamValue>>;
  readonly eventName?: string;
  readonly onMessage: (raw: string) => void;
  readonly onOpen?: () => void;
  readonly onError?: (err: Error) => void;
}

export interface SseSubscription {
  close(): void;
}

export class UnauthorizedError extends Error {
  readonly status = 401;

  constructor(detail?: string) {
    super(detail || "Request failed with 401");
    this.name = "UnauthorizedError";
  }
}

/**
 * Non-401 HTTP failure with the response's structure preserved. FastAPI
 * services serialise errors as `{"detail": <string | object>}`; object
 * details carry a machine-readable `code` (e.g. the account-linking
 * `merge_required` / `last_sign_in_method` 409s) that hosts branch on.
 * `message` is always the best human-readable line we could extract
 * (string detail → itself; object detail → its `safe_message`; else the
 * raw body / a status fallback), so existing `err.message` consumers keep
 * working unchanged.
 */
export class TransportHttpError extends Error {
  readonly status: number;
  /** Parsed `detail` — string, structured object, or null (non-JSON body). */
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown = null) {
    super(message || `Request failed with ${status}`);
    this.name = "TransportHttpError";
    this.status = status;
    this.detail = detail;
  }

  /** The structured detail's `code`, when the server sent one. */
  get code(): string | null {
    if (
      typeof this.detail === "object" &&
      this.detail !== null &&
      "code" in this.detail &&
      typeof (this.detail as { code: unknown }).code === "string"
    ) {
      return (this.detail as { code: string }).code;
    }
    return null;
  }
}

/**
 * Narrowing helper that also matches errors rehydrated across a realm
 * boundary (the desktop IPC hop re-creates the instance, so `instanceof`
 * is reliable there — but keep the duck-type check for safety with
 * multiple copies of this package in one bundle).
 */
export function isTransportHttpError(err: unknown): err is TransportHttpError {
  return (
    err instanceof TransportHttpError ||
    (typeof err === "object" &&
      err !== null &&
      (err as { name?: unknown }).name === "TransportHttpError" &&
      typeof (err as { status?: unknown }).status === "number")
  );
}
