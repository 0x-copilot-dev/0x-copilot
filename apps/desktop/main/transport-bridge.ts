import type {
  ArtifactContentRequest,
  ArtifactContentResponse,
  ArtifactRevisionRequest,
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { isArtifactTransport } from "@0x-copilot/chat-transport";

import type { StreamEventPayload } from "./ipc/schemas";

export type StreamEventEmitter = (
  webContentsId: number,
  payload: StreamEventPayload,
) => void;

interface SubscriptionHandle {
  readonly webContentsId: number;
  readonly subscription: SseSubscription;
}

export interface TransportBridgeOptions {
  // Bearer attachment + 401 refresh are responsibilities of the transport
  // decorator chain (WebTransport.bearerProvider + withBearerRefresh).
  // The bridge bridges IPC <-> Transport and owns subscription lifecycle —
  // nothing else. Required so a wiring bug can't silently inject a
  // fixture into a shipped binary.
  readonly transport: Transport;
  // PRD-10 tier-2 tap. When set, every SSE run-feed message is also forwarded
  // here (in addition to the renderer) so the main-process tier-2 lifecycle can
  // observe `adapter_generated` events off the same stream the UI consumes. A
  // pure observer: it must never throw (a throw here would break UI delivery).
  readonly onRunFeedMessage?: (raw: string) => void;
}

export class TransportBridge {
  readonly #transport: Transport;
  readonly #emit: StreamEventEmitter;
  readonly #onRunFeedMessage?: (raw: string) => void;
  readonly #subscriptions = new Map<string, SubscriptionHandle>();
  readonly #artifactReaders = new Map<
    string,
    ReadableStreamDefaultReader<Uint8Array>
  >();
  #nextArtifactHandle = 1;

  constructor(emit: StreamEventEmitter, options: TransportBridgeOptions) {
    this.#transport = options.transport;
    this.#emit = emit;
    this.#onRunFeedMessage = options.onRunFeedMessage;
  }

  async request<T>(req: TypedRequest): Promise<T> {
    return this.#transport.request<T>(req);
  }

  async openArtifactContent(
    request: ArtifactContentRequest,
  ): Promise<
    Omit<ArtifactContentResponse, "body"> & { readonly handle: string }
  > {
    if (!isArtifactTransport(this.#transport)) {
      throw new Error("This desktop transport does not support artifact bytes");
    }
    const content = await this.#transport.getArtifactContent(request);
    const handle = `artifact-stream-${this.#nextArtifactHandle++}`;
    this.#artifactReaders.set(handle, content.body.getReader());
    const { body: _body, ...metadata } = content;
    return { handle, ...metadata };
  }

  async readArtifactContent(
    handle: string,
  ): Promise<{ readonly done: boolean; readonly chunk: Uint8Array | null }> {
    const reader = this.#artifactReaders.get(handle);
    if (!reader) throw new Error("Artifact stream is no longer available");
    const next = await reader.read();
    if (next.done) {
      this.#artifactReaders.delete(handle);
      reader.releaseLock();
      return { done: true, chunk: null };
    }
    return { done: false, chunk: next.value };
  }

  async closeArtifactContent(handle: string): Promise<void> {
    const reader = this.#artifactReaders.get(handle);
    if (!reader) return;
    this.#artifactReaders.delete(handle);
    await reader.cancel();
    reader.releaseLock();
  }

  async createArtifactRevision(
    request: ArtifactRevisionRequest,
  ): Promise<unknown> {
    if (!isArtifactTransport(this.#transport)) {
      throw new Error(
        "This desktop transport does not support artifact revisions",
      );
    }
    return this.#transport.createArtifactRevision(request);
  }

  sessionSnapshot(): {
    session: Session;
    capabilities: TransportCapabilities;
  } {
    return {
      session: this.#transport.getSession(),
      capabilities: this.#transport.capabilities(),
    };
  }

  subscribe(
    subscriptionId: string,
    webContentsId: number,
    opts: Pick<SseSubscribeOptions, "path" | "query" | "eventName">,
  ): void {
    if (this.#subscriptions.has(subscriptionId)) {
      throw new Error(`subscriptionId "${subscriptionId}" already active`);
    }
    const subscription = this.#transport.subscribeServerSentEvents({
      path: opts.path,
      query: opts.query,
      eventName: opts.eventName,
      onOpen: () => {
        this.#emit(webContentsId, { subscriptionId, kind: "open" });
      },
      onMessage: (raw: string) => {
        this.#emit(webContentsId, {
          subscriptionId,
          kind: "message",
          message: raw,
        });
        // PRD-10 tap — never let an observer failure break UI delivery.
        if (this.#onRunFeedMessage) {
          try {
            this.#onRunFeedMessage(raw);
          } catch {
            // best-effort observer; swallow.
          }
        }
      },
      onError: (err: Error) => {
        this.#emit(webContentsId, {
          subscriptionId,
          kind: "error",
          errorMessage: err.message,
        });
      },
    });
    this.#subscriptions.set(subscriptionId, { webContentsId, subscription });
  }

  unsubscribe(subscriptionId: string): boolean {
    const handle = this.#subscriptions.get(subscriptionId);
    if (!handle) return false;
    handle.subscription.close();
    this.#subscriptions.delete(subscriptionId);
    this.#emit(handle.webContentsId, { subscriptionId, kind: "closed" });
    return true;
  }

  unsubscribeForWebContents(webContentsId: number): void {
    for (const [id, handle] of this.#subscriptions) {
      if (handle.webContentsId === webContentsId) {
        handle.subscription.close();
        this.#subscriptions.delete(id);
      }
    }
  }

  closeAll(): void {
    for (const [, handle] of this.#subscriptions) {
      handle.subscription.close();
    }
    this.#subscriptions.clear();
    for (const [, reader] of this.#artifactReaders) {
      void reader.cancel();
      reader.releaseLock();
    }
    this.#artifactReaders.clear();
  }

  activeSubscriptionCount(): number {
    return this.#subscriptions.size;
  }
}
