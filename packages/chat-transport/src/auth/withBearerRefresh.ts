import type { ArtifactCapableTransport, Transport } from "../transport";
import {
  type ArtifactContentRequest,
  type ArtifactContentResponse,
  type ArtifactRevisionRequest,
  type Session,
  type SseSubscribeOptions,
  type SseSubscription,
  type TransportCapabilities,
  type TypedRequest,
  UnauthorizedError,
  isArtifactTransport,
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
  inner: ArtifactCapableTransport,
  opts: WithBearerRefreshOptions,
): ArtifactCapableTransport;
export function withBearerRefresh(
  inner: Transport,
  opts: WithBearerRefreshOptions,
): Transport;
export function withBearerRefresh(
  inner: Transport,
  opts: WithBearerRefreshOptions,
): Transport {
  if (isArtifactTransport(inner)) {
    return new ArtifactBearerRefreshTransport(inner, opts);
  }
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
    return this.executeWithRefresh(
      () => this.#inner.request<TRes>(req),
      () => this.#safeNotifyRetry(req),
    );
  }

  protected async executeWithRefresh<T>(
    operation: () => Promise<T>,
    onRetry: () => void = () => {},
  ): Promise<T> {
    try {
      return await operation();
    } catch (err) {
      if (!(err instanceof UnauthorizedError)) {
        throw err;
      }
      const result = await this.#refresh(this.#workspaceId);
      if (!result.ok) {
        this.#safeNotifyRefreshFailure(result.reason ?? "refresh failed");
        throw err;
      }
      onRetry();
      // Single retry only — a second UnauthorizedError propagates so the
      // renderer's sign-in surface re-prompts instead of looping.
      return await operation();
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

class ArtifactBearerRefreshTransport
  extends BearerRefreshTransport
  implements ArtifactCapableTransport
{
  readonly #artifactInner: ArtifactCapableTransport;

  constructor(inner: ArtifactCapableTransport, opts: WithBearerRefreshOptions) {
    super(inner, opts);
    this.#artifactInner = inner;
  }

  async getArtifactContent(
    request: ArtifactContentRequest,
  ): Promise<ArtifactContentResponse> {
    return this.executeWithRefresh(() =>
      this.#artifactInner.getArtifactContent(request),
    );
  }

  async createArtifactRevision(
    request: ArtifactRevisionRequest,
  ): Promise<unknown> {
    return this.executeWithRefresh(() =>
      this.#artifactInner.createArtifactRevision(request),
    );
  }
}
