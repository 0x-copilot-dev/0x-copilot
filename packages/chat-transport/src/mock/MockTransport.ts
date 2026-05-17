import type { Transport } from "../transport";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  TransportCapabilities,
  TypedRequest,
} from "../types";
import { EMAIL_FIXTURE } from "./email-fixture";

type TimeoutHandle = unknown;
type SetTimeoutImpl = (handler: () => void, ms: number) => TimeoutHandle;
type ClearTimeoutImpl = (handle: TimeoutHandle) => void;

export interface MockTransportConfig {
  // Injection points so tests can drive virtual time via vi.useFakeTimers
  // without having to monkey-patch globals. Defaults bind to globalThis.
  readonly setTimeoutImpl?: SetTimeoutImpl;
  readonly clearTimeoutImpl?: ClearTimeoutImpl;
  readonly capabilities?: Partial<TransportCapabilities>;
  readonly session?: Session;
}

interface ScheduledEvent {
  readonly delayMs: number;
  readonly payload: unknown;
}

const EMAIL_DRAFT_PATH = "/drafts/draft-1";
const EMAIL_EVENT_PATH = "/drafts/draft-1/events";

const DEFAULT_CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

const DEFAULT_SESSION: Session = { bearer: null };

export class MockTransport implements Transport {
  readonly #setTimeoutImpl: SetTimeoutImpl;
  readonly #clearTimeoutImpl: ClearTimeoutImpl;
  readonly #capabilities: TransportCapabilities;
  readonly #session: Session;

  constructor(config: MockTransportConfig = {}) {
    this.#setTimeoutImpl =
      config.setTimeoutImpl ??
      ((handler, ms) => globalThis.setTimeout(handler, ms));
    this.#clearTimeoutImpl =
      config.clearTimeoutImpl ??
      ((handle) =>
        globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>));
    this.#capabilities = { ...DEFAULT_CAPABILITIES, ...config.capabilities };
    this.#session = config.session ?? DEFAULT_SESSION;
  }

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    if (req.path === EMAIL_DRAFT_PATH) {
      const { draft } = EMAIL_FIXTURE;
      return {
        draftId: draft.draftId,
        to: draft.to,
        cc: draft.cc,
        subject: draft.subject,
        bodyPrefix: draft.bodyPrefix,
        bodySuffix: draft.bodySuffix,
      } as TRes;
    }
    throw new Error(`MockTransport: no fixture for ${req.method} ${req.path}`);
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    if (opts.path !== EMAIL_EVENT_PATH) {
      throw new Error(`MockTransport: no event fixture for ${opts.path}`);
    }
    return this.#scheduleEmailStream(opts);
  }

  getSession(): Session {
    return this.#session;
  }

  capabilities(): TransportCapabilities {
    return this.#capabilities;
  }

  #scheduleEmailStream(opts: SseSubscribeOptions): SseSubscription {
    const events = buildEmailEventSchedule();
    const handles: TimeoutHandle[] = [];
    let closed = false;
    let openFired = false;

    const fireOpen = (): void => {
      if (!openFired && !closed) {
        openFired = true;
        opts.onOpen?.();
      }
    };

    // onOpen has to fire before any messages do (consumers wire UI on open).
    // Schedule it at delay 0 so it lands on the same tick as the first
    // event but strictly before it.
    handles.push(
      this.#setTimeoutImpl(() => {
        fireOpen();
      }, 0),
    );

    for (const evt of events) {
      const handle = this.#setTimeoutImpl(() => {
        if (closed) return;
        fireOpen();
        try {
          opts.onMessage(JSON.stringify(evt.payload));
        } catch (err) {
          opts.onError?.(err instanceof Error ? err : new Error(String(err)));
        }
      }, evt.delayMs);
      handles.push(handle);
    }

    return {
      close: (): void => {
        if (closed) return;
        closed = true;
        for (const h of handles) {
          this.#clearTimeoutImpl(h);
        }
      },
    };
  }
}

export function buildEmailEventSchedule(): readonly ScheduledEvent[] {
  const { draft, streamingBodyChunks, pendingDiff } = EMAIL_FIXTURE;
  const events: ScheduledEvent[] = [];
  events.push({
    delayMs: 0,
    payload: {
      type: "tool_call_start",
      callId: "call-1",
      tool: "Gmail.draft.create",
      args: {
        to: draft.to,
        cc: draft.cc,
        subject: draft.subject,
      },
    },
  });
  const chunkDelays = [400, 800, 1200, 1600, 2000];
  for (let i = 0; i < streamingBodyChunks.length; i++) {
    events.push({
      delayMs: chunkDelays[i] ?? 400 * (i + 1),
      payload: {
        type: "tool_call_chunk",
        callId: "call-1",
        sequenceNo: i,
        chunk: streamingBodyChunks[i],
        progressPercent: ((i + 1) / streamingBodyChunks.length) * 100,
      },
    });
  }
  events.push({
    delayMs: 2400,
    payload: {
      type: "tool_call_end",
      callId: "call-1",
    },
  });
  events.push({
    delayMs: 2700,
    payload: {
      type: "pending_diff_appeared",
      diffId: pendingDiff.diffId,
      provenance: pendingDiff.provenance,
      title: pendingDiff.title,
      description: pendingDiff.description,
      regionAnchorId: pendingDiff.regionAnchorId,
    },
  });
  return events;
}
