import type { Transport } from "../transport";
import {
  type Session,
  type SseSubscribeOptions,
  type SseSubscription,
  type TransportCapabilities,
  type TypedRequest,
  UnauthorizedError,
} from "../types";

export interface BearerRefreshResult {
  readonly ok: boolean;
  readonly reason?: string;
}

export type BearerRefreshFn = (
  workspaceId: string,
) => Promise<BearerRefreshResult>;

export interface WithBearerRefreshOptions {
  readonly workspaceId: string;
  readonly refresh: BearerRefreshFn;
  readonly onUnauthorizedRetry?: (req: TypedRequest) => void;
  readonly onRefreshFailure?: (reason: string) => void;
}

export function withBearerRefresh(
  inner: Transport,
  opts: WithBearerRefreshOptions,
): Transport {
  return new BearerRefreshTransport(inner, opts);
}

class BearerRefreshTransport implements Transport {
  readonly #inner: Transport;
  readonly #workspaceId: string;
  readonly #refresh: BearerRefreshFn;
  readonly #onUnauthorizedRetry: (req: TypedRequest) => void;
  readonly #onRefreshFailure: (reason: string) => void;

  constructor(inner: Transport, opts: WithBearerRefreshOptions) {
    this.#inner = inner;
    this.#workspaceId = opts.workspaceId;
    this.#refresh = opts.refresh;
    this.#onUnauthorizedRetry = opts.onUnauthorizedRetry ?? (() => {});
    this.#onRefreshFailure = opts.onRefreshFailure ?? (() => {});
  }

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    try {
      return await this.#inner.request<TRes>(req);
    } catch (err) {
      if (!(err instanceof UnauthorizedError)) {
        throw err;
      }
      const result = await this.#refresh(this.#workspaceId);
      if (!result.ok) {
        this.#safeNotifyRefreshFailure(result.reason ?? "refresh failed");
        throw err;
      }
      this.#safeNotifyRetry(req);
      // Single retry only — a second UnauthorizedError propagates so the
      // renderer's sign-in surface re-prompts instead of looping.
      return await this.#inner.request<TRes>(req);
    }
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    return this.#inner.subscribeServerSentEvents(opts);
  }

  getSession(): Session {
    return this.#inner.getSession();
  }

  capabilities(): TransportCapabilities {
    return this.#inner.capabilities();
  }

  #safeNotifyRetry(req: TypedRequest): void {
    try {
      this.#onUnauthorizedRetry(req);
    } catch {
      // observer errors must not mask the auth flow
    }
  }

  #safeNotifyRefreshFailure(reason: string): void {
    try {
      this.#onRefreshFailure(reason);
    } catch {
      // observer errors must not mask the auth flow
    }
  }
}
