import type { Transport } from "../transport";
import {
  type QueryParamValue,
  type Session,
  type SseSubscribeOptions,
  type SseSubscription,
  type TransportCapabilities,
  type TypedRequest,
  TransportHttpError,
  UnauthorizedError,
} from "../types";
import { runSseStream } from "./sse";

type BearerProvider = () => string | null;
type UnauthorizedHandler = (response: Response) => void;
type FetchFn = typeof fetch;

export interface WebTransportConfig {
  /** URL prefix prepended to every request path. Empty for same-origin. */
  readonly baseUrl?: string;
  /**
   * Source of the bearer token attached as `Authorization: Bearer …`. Read
   * on every call so rotated tokens are picked up without reconfiguring the
   * transport.
   */
  readonly bearerProvider?: BearerProvider;
  /**
   * Notified once per 401 before `UnauthorizedError` is thrown. Errors from
   * the handler are swallowed so they don't mask the original auth failure.
   */
  readonly onUnauthorized?: UnauthorizedHandler;
  /** Override for tests; defaults to global fetch bound to globalThis. */
  readonly fetch?: FetchFn;
}

const REQUEST_ID_HEADER = "x-request-id";
const AUTHORIZATION_HEADER = "authorization";
const JSON_CONTENT_TYPE = "application/json";

export class WebTransport implements Transport {
  readonly #baseUrl: string;
  readonly #bearerProvider: BearerProvider;
  readonly #onUnauthorized: UnauthorizedHandler;
  readonly #fetchOverride: FetchFn | undefined;

  constructor(config: WebTransportConfig = {}) {
    this.#baseUrl = config.baseUrl ?? "";
    this.#bearerProvider = config.bearerProvider ?? (() => null);
    this.#onUnauthorized = config.onUnauthorized ?? (() => {});
    this.#fetchOverride = config.fetch;
  }

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    const url = this.#buildUrl(req.path, req.query);
    const init: RequestInit = {
      method: req.method,
      headers: this.#buildHeaders(req),
      signal: req.signal,
    };
    if (req.body !== undefined) {
      init.body = JSON.stringify(req.body);
    }
    const response = await this.#doFetch(url, init);
    return this.#parseResponse<TRes>(response);
  }

  // Resolve fetch on every call rather than at construction so test code
  // that replaces globalThis.fetch via vi.spyOn (after the transport is
  // already constructed) still intercepts requests. The override branch
  // remains for dependency injection in non-spy tests.
  #doFetch(url: string, init: RequestInit): Promise<Response> {
    if (this.#fetchOverride) {
      return this.#fetchOverride(url, init);
    }
    return globalThis.fetch(url, init);
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    return runSseStream({
      url: this.#buildUrl(opts.path, opts.query),
      headers: this.#baseHeaders(),
      eventName: opts.eventName ?? "message",
      onMessage: opts.onMessage,
      onOpen: opts.onOpen,
      onError: opts.onError,
      // Deferred lookup mirrors #doFetch — test-time vi.spyOn replacements
      // of globalThis.fetch must still intercept SSE requests.
      fetchImpl: (input, init) =>
        this.#doFetch(input as string, init as RequestInit),
    });
  }

  getSession(): Session {
    return { bearer: this.#bearerProvider() };
  }

  capabilities(): TransportCapabilities {
    return {
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: true,
      openExternal: true,
    };
  }

  #buildUrl(
    path: string,
    query: Readonly<Record<string, QueryParamValue>> | undefined,
  ): string {
    const base = this.#baseUrl ? this.#baseUrl + path : path;
    if (!query) {
      return base;
    }
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) {
        params.set(key, String(value));
      }
    }
    const qs = params.toString();
    return qs ? `${base}?${qs}` : base;
  }

  // Shared by request() and subscribeServerSentEvents(): a fresh request-id
  // plus the bearer when a session is active. The two callers layer their
  // own headers on top (content-type for request, accept for SSE).
  #baseHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      [REQUEST_ID_HEADER]: newRequestId(),
    };
    const bearer = this.#bearerProvider();
    if (bearer) {
      headers[AUTHORIZATION_HEADER] = `Bearer ${bearer}`;
    }
    return headers;
  }

  #buildHeaders(req: TypedRequest): Record<string, string> {
    const headers = this.#baseHeaders();
    if (req.body !== undefined) {
      headers["content-type"] = JSON_CONTENT_TYPE;
    }
    if (req.headers) {
      for (const [k, v] of Object.entries(req.headers)) {
        headers[k] = v;
      }
    }
    return headers;
  }

  async #parseResponse<TRes>(response: Response): Promise<TRes> {
    if (response.ok) {
      if (response.status === 204) {
        return undefined as TRes;
      }
      const text = await response.text();
      if (!text) {
        return undefined as TRes;
      }
      return JSON.parse(text) as TRes;
    }
    const body = await response.text();
    const { message, detail } = parseFastApiError(body, response.status);
    if (response.status === 401) {
      try {
        this.#onUnauthorized(response);
      } catch {
        // handler errors must not mask the original 401
      }
      throw new UnauthorizedError(message);
    }
    throw new TransportHttpError(response.status, message, detail);
  }
}

// FastAPI / Starlette serialises errors as `{"detail": <string | object>}`.
// Extract the best human-readable message so callers don't render raw
// JSON, and keep the parsed detail so callers can branch on structured
// codes (TransportHttpError.detail / .code). Non-JSON bodies (proxy
// timeouts, HTML error pages) fall through to the verbatim text.
function parseFastApiError(
  body: string,
  status: number,
): { message: string; detail: unknown } {
  const fallback = body || `Request failed with ${status}`;
  if (!body || body[0] !== "{") {
    return { message: fallback, detail: null };
  }
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    const detail = parsed.detail ?? null;
    if (typeof detail === "string" && detail.trim() !== "") {
      return { message: detail, detail };
    }
    if (typeof detail === "object" && detail !== null) {
      const safeMessage = (detail as { safe_message?: unknown }).safe_message;
      return {
        message:
          typeof safeMessage === "string" && safeMessage.trim() !== ""
            ? safeMessage
            : fallback,
        detail,
      };
    }
  } catch {
    // not JSON; fall through
  }
  return { message: fallback, detail: null };
}

function newRequestId(): string {
  const cryptoObj =
    typeof globalThis.crypto !== "undefined" ? globalThis.crypto : undefined;
  const random =
    cryptoObj && typeof cryptoObj.randomUUID === "function"
      ? cryptoObj.randomUUID().replace(/-/g, "")
      : Math.random().toString(16).slice(2).padEnd(32, "0");
  return `req_${random}`;
}
