import type { Transport } from "../transport";
import {
  type QueryParamValue,
  type Session,
  type SseSubscribeOptions,
  type SseSubscription,
  type TransportCapabilities,
  type TypedRequest,
  UnauthorizedError,
} from "../types";

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
  readonly #fetch: FetchFn;

  constructor(config: WebTransportConfig = {}) {
    this.#baseUrl = config.baseUrl ?? "";
    this.#bearerProvider = config.bearerProvider ?? (() => null);
    this.#onUnauthorized = config.onUnauthorized ?? (() => {});
    this.#fetch = config.fetch ?? globalThis.fetch.bind(globalThis);
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
    const response = await this.#fetch(url, init);
    return this.#parseResponse<TRes>(response);
  }

  subscribeServerSentEvents(_opts: SseSubscribeOptions): SseSubscription {
    // Wired in PR #3 when the existing _streamSseEvents helper moves into
    // packages/chat-transport/src/web/sse.ts. Until then nothing in
    // apps/frontend uses Transport for SSE, so this stub is unreachable.
    throw new Error(
      "WebTransport.subscribeServerSentEvents: not yet wired (rollout plan PR #3)",
    );
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

  #buildHeaders(req: TypedRequest): Record<string, string> {
    const headers: Record<string, string> = {
      [REQUEST_ID_HEADER]: newRequestId(),
    };
    if (req.body !== undefined) {
      headers["content-type"] = JSON_CONTENT_TYPE;
    }
    const bearer = this.#bearerProvider();
    if (bearer) {
      headers[AUTHORIZATION_HEADER] = `Bearer ${bearer}`;
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
    const message = parseFastApiDetail(body) ?? body;
    if (response.status === 401) {
      try {
        this.#onUnauthorized(response);
      } catch {
        // handler errors must not mask the original 401
      }
      throw new UnauthorizedError(message);
    }
    throw new Error(message || `Request failed with ${response.status}`);
  }
}

// FastAPI / Starlette serialises errors as `{"detail": "…"}`. Pull the
// message out so callers don't render raw JSON; non-JSON bodies (proxy
// timeouts, HTML error pages) fall through to the verbatim text.
function parseFastApiDetail(body: string): string | null {
  if (!body || body[0] !== "{") {
    return null;
  }
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim() !== "") {
      return parsed.detail;
    }
  } catch {
    // not JSON; fall through
  }
  return null;
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
