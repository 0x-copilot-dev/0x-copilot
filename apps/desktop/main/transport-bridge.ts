import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";

import type { StreamEventPayload } from "./ipc/schemas";

// Pushes a stream event back to a renderer's webContents on the allowlisted
// stream-event channel. Injected so the bridge can be unit-tested without
// spinning up Electron. Production wires this to
// webContents.send(CHANNELS.streamEvent, payload).
export type StreamEventEmitter = (
  webContentsId: number,
  payload: StreamEventPayload,
) => void;

interface SubscriptionHandle {
  readonly webContentsId: number;
  readonly subscription: SseSubscription;
}

export interface TransportBridgeOptions {
  // The bridge does not pick a transport — apps/desktop/main/index.ts does,
  // based on env (MockTransport for dev, WebTransport wrapped with
  // withBearerRefresh for production). Required so a wiring bug can't
  // silently inject a fixture into a shipped binary.
  readonly transport: Transport;
}

export class TransportBridge {
  readonly #transport: Transport;
  readonly #emit: StreamEventEmitter;
  readonly #subscriptions = new Map<string, SubscriptionHandle>();

  constructor(emit: StreamEventEmitter, options: TransportBridgeOptions) {
    this.#transport = options.transport;
    this.#emit = emit;
  }

  async request<T>(req: TypedRequest): Promise<T> {
    return this.#transport.request<T>(req);
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

  // Agent 1-A wires this to webContents.on('destroyed') in window.ts.
  unsubscribeForWebContents(webContentsId: number): void {
    for (const [id, handle] of this.#subscriptions) {
      if (handle.webContentsId === webContentsId) {
        handle.subscription.close();
        this.#subscriptions.delete(id);
      }
    }
  }

  // Called at app shutdown.
  closeAll(): void {
    for (const [, handle] of this.#subscriptions) {
      handle.subscription.close();
    }
    this.#subscriptions.clear();
  }

  activeSubscriptionCount(): number {
    return this.#subscriptions.size;
  }
}
