import type { Transport } from "../transport";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  TransportCapabilities,
  TypedRequest,
} from "../types";
import {
  CHANNELS,
  unwrapTransportResult,
  type StreamEventPayload,
} from "./rpc-protocol";
import type { WindowBridge } from "./window-bridge";

export interface IpcTransportConfig {
  readonly bridge: WindowBridge;
  // Cached at construction. Production fetches once at sign-in (Phase 5);
  // Phase 1 stub passes static values. getSession() / capabilities() on the
  // on-disk Transport contract are synchronous accessors, so they cannot
  // round-trip on every call without breaking substitution with WebTransport.
  readonly bootstrapSession: Session;
  readonly bootstrapCapabilities: TransportCapabilities;
  // Test seam. Defaults to globalThis.crypto.randomUUID() — the prefix
  // marks the only substrate touchpoint in this module per PRD §6.5.
  readonly randomId?: () => string;
}

interface SubscriptionRecord {
  readonly opts: SseSubscribeOptions;
  open: boolean;
}

// Max events held while waiting for a not-yet-registered subscriptionId.
// Caps the worst case if main ever emits for a phantom id.
const PENDING_BUFFER_CAP = 16;

function defaultRandomId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  return `sub-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// Renderer-side Transport. Every method proxies to main via window.bridge.
//
// SseSubscription returns synchronously per the on-disk Transport contract,
// so we generate the subscriptionId locally and fire the IPC in the
// background. Subscribe-time errors arrive on the stream-event channel as
// kind: "error" — same shape a backend SSE error would take. The renderer's
// subscription record is set synchronously before the IPC fires, so any
// stream-event that arrives back for that id always finds it.
//
// One belt over the synchronous-registration suspenders: stream events for
// unknown subscriptionIds are buffered for a single microtask flush and
// re-dispatched. This catches the (theoretical) case where main emits an
// event before the renderer has finished its own setup — e.g. a re-mount
// under <StrictMode> where the cleanup deleted the record while a stream-
// event was in flight, and the re-subscribe is still being processed.
// After the microtask, still-unknown events are dropped. The buffer has a
// hard cap so a misbehaving main can't grow it without bound.
export class IpcTransport implements Transport {
  readonly #bridge: WindowBridge;
  readonly #session: Session;
  readonly #capabilities: TransportCapabilities;
  readonly #randomId: () => string;
  readonly #subscriptions = new Map<string, SubscriptionRecord>();
  readonly #pendingEvents: StreamEventPayload[] = [];
  #pendingFlushScheduled = false;
  readonly #removeStreamListener: () => void;

  constructor(config: IpcTransportConfig) {
    this.#bridge = config.bridge;
    this.#session = config.bootstrapSession;
    this.#capabilities = config.bootstrapCapabilities;
    this.#randomId = config.randomId ?? defaultRandomId;
    this.#removeStreamListener = this.#bridge.ipc.on(
      CHANNELS.streamEvent,
      (raw: unknown) => {
        this.#receiveStreamEvent(raw);
      },
    );
  }

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    // signal is renderer-local. AbortSignal isn't structured-clone-friendly
    // across IPC. Production needs a token-based cancel side channel —
    // flagged for Phase 5.
    const { signal: _signal, ...payload } = req;
    void _signal;
    // Main resolves with the transport-result envelope so structured HTTP
    // failures (status + FastAPI detail) survive the IPC hop; unwrap
    // rehydrates TransportHttpError / UnauthorizedError. Bare values from
    // older mains / test doubles pass through unchanged.
    const raw = await this.#bridge.ipc.invoke<unknown>(
      CHANNELS.transportRequest,
      payload,
    );
    return unwrapTransportResult<TRes>(raw);
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    const subscriptionId = this.#randomId();
    // Sync registration MUST happen before invoke. Any stream-event for
    // this id arrives after this line and finds the record.
    this.#subscriptions.set(subscriptionId, { opts, open: false });

    void this.#bridge.ipc
      .invoke(CHANNELS.transportSubscribe, {
        subscriptionId,
        path: opts.path,
        query: opts.query,
        eventName: opts.eventName,
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        const record = this.#subscriptions.get(subscriptionId);
        if (record) {
          this.#subscriptions.delete(subscriptionId);
          record.opts.onError?.(new Error(`subscribe failed: ${message}`));
        }
      });

    return {
      close: (): void => {
        const existed = this.#subscriptions.delete(subscriptionId);
        if (!existed) return;
        void this.#bridge.ipc
          .invoke(CHANNELS.transportUnsubscribe, { subscriptionId })
          .catch(() => {
            // Best-effort. SseSubscription.close() returns void.
          });
      },
    };
  }

  getSession(): Session {
    return this.#session;
  }

  capabilities(): TransportCapabilities {
    return this.#capabilities;
  }

  // Renderer-side teardown hook for hot-reload / window close. Not part of
  // the Transport contract — bootstrap (Agent 1-A) is expected to call
  // this when the React root unmounts.
  dispose(): void {
    this.#removeStreamListener();
    for (const subscriptionId of [...this.#subscriptions.keys()]) {
      this.#subscriptions.delete(subscriptionId);
      void this.#bridge.ipc
        .invoke(CHANNELS.transportUnsubscribe, { subscriptionId })
        .catch(() => {});
    }
    this.#pendingEvents.length = 0;
  }

  #receiveStreamEvent(raw: unknown): void {
    const event = this.#asStreamEvent(raw);
    if (!event) return;
    if (this.#subscriptions.has(event.subscriptionId)) {
      this.#dispatchKnown(event);
      return;
    }
    // Unknown id. Buffer briefly in case the renderer-side setup is mid-
    // microtask. Cap protects against unbounded growth.
    if (this.#pendingEvents.length >= PENDING_BUFFER_CAP) {
      return;
    }
    this.#pendingEvents.push(event);
    this.#schedulePendingFlush();
  }

  #schedulePendingFlush(): void {
    if (this.#pendingFlushScheduled) return;
    this.#pendingFlushScheduled = true;
    queueMicrotask(() => {
      this.#pendingFlushScheduled = false;
      const drained = this.#pendingEvents.splice(0, this.#pendingEvents.length);
      for (const event of drained) {
        if (this.#subscriptions.has(event.subscriptionId)) {
          this.#dispatchKnown(event);
        }
        // Still unknown after the microtask → drop. The owning subscribe
        // never registered (or was cleaned up before the event landed).
      }
    });
  }

  #dispatchKnown(event: StreamEventPayload): void {
    const record = this.#subscriptions.get(event.subscriptionId);
    if (!record) return;
    switch (event.kind) {
      case "open":
        if (!record.open) {
          record.open = true;
          record.opts.onOpen?.();
        }
        break;
      case "message":
        if (typeof event.message === "string") {
          record.opts.onMessage(event.message);
        }
        break;
      case "error":
        record.opts.onError?.(new Error(event.errorMessage ?? "stream error"));
        break;
      case "closed":
        this.#subscriptions.delete(event.subscriptionId);
        break;
    }
  }

  #asStreamEvent(raw: unknown): StreamEventPayload | null {
    if (!raw || typeof raw !== "object") return null;
    const r = raw as Record<string, unknown>;
    if (typeof r.subscriptionId !== "string" || r.subscriptionId.length === 0) {
      return null;
    }
    if (
      r.kind !== "open" &&
      r.kind !== "message" &&
      r.kind !== "error" &&
      r.kind !== "closed"
    ) {
      return null;
    }
    return {
      subscriptionId: r.subscriptionId,
      kind: r.kind,
      message: typeof r.message === "string" ? r.message : undefined,
      errorMessage:
        typeof r.errorMessage === "string" ? r.errorMessage : undefined,
    };
  }
}
