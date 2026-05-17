import { describe, expect, it, vi } from "vitest";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";

import { TransportBridge } from "../transport-bridge";
import {
  CHANNELS,
  IpcValidationError,
  type StreamEventPayload,
} from "./schemas";
import { registerIpcHandlers } from "./handlers";

// Fake ipcMain that records handlers and lets tests invoke them directly
// with a synthetic IpcMainInvokeEvent. Avoids spinning up Electron.
function makeFakeIpcMain() {
  const handlers = new Map<
    string,
    (event: { sender: { id: number } }, raw: unknown) => unknown
  >();
  return {
    handle(
      channel: string,
      fn: (event: { sender: { id: number } }, raw: unknown) => unknown,
    ) {
      handlers.set(channel, fn);
    },
    removeHandler(channel: string) {
      handlers.delete(channel);
    },
    async invoke(channel: string, webContentsId: number, payload: unknown) {
      const fn = handlers.get(channel);
      if (!fn) throw new Error(`no handler for ${channel}`);
      return fn({ sender: { id: webContentsId } }, payload);
    },
    has(channel: string) {
      return handlers.has(channel);
    },
    handlerCount() {
      return handlers.size;
    },
  };
}

class FakeTransport implements Transport {
  readonly requestCalls: TypedRequest[] = [];
  readonly subscriptions: Array<{
    opts: SseSubscribeOptions;
    handle: SseSubscription;
    closed: boolean;
  }> = [];
  shouldThrowOnSubscribe = false;

  async request<T>(req: TypedRequest): Promise<T> {
    this.requestCalls.push(req);
    return { ok: true, path: req.path } as T;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    if (this.shouldThrowOnSubscribe) {
      throw new Error("transport refuses subscription");
    }
    const record = {
      opts,
      handle: { close: () => {} },
      closed: false,
    };
    record.handle = {
      close: () => {
        record.closed = true;
      },
    };
    this.subscriptions.push(record);
    return record.handle;
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return {
      substrate: "desktop-webview",
      nativeSecretStorage: true,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    };
  }
}

function setup() {
  const ipcMain = makeFakeIpcMain();
  const transport = new FakeTransport();
  const events: Array<{
    webContentsId: number;
    payload: StreamEventPayload;
  }> = [];
  const bridge = new TransportBridge(
    (webContentsId, payload) => events.push({ webContentsId, payload }),
    { transport },
  );
  const logger = { info: vi.fn(), warn: vi.fn() };
  const teardown = registerIpcHandlers({
    ipcMain: ipcMain as unknown as Parameters<
      typeof registerIpcHandlers
    >[0]["ipcMain"],
    bridge,
    logger,
  });
  return { ipcMain, transport, events, bridge, logger, teardown };
}

describe("registerIpcHandlers — channel registration", () => {
  it("registers every documented channel", () => {
    const { ipcMain } = setup();
    expect(ipcMain.has(CHANNELS.transportRequest)).toBe(true);
    expect(ipcMain.has(CHANNELS.transportSubscribe)).toBe(true);
    expect(ipcMain.has(CHANNELS.transportUnsubscribe)).toBe(true);
    expect(ipcMain.has(CHANNELS.transportSessionSnapshot)).toBe(true);
  });

  it("teardown removes all handlers and closes active subscriptions", async () => {
    const { ipcMain, bridge, teardown, transport } = setup();
    await ipcMain.invoke(CHANNELS.transportSubscribe, 1, {
      subscriptionId: "sub-1",
      path: "/x",
    });
    expect(bridge.activeSubscriptionCount()).toBe(1);
    teardown();
    expect(ipcMain.handlerCount()).toBe(0);
    expect(transport.subscriptions[0].closed).toBe(true);
  });
});

describe("transport.request", () => {
  it("validates and forwards to bridge.request", async () => {
    const { ipcMain, transport } = setup();
    const res = (await ipcMain.invoke(CHANNELS.transportRequest, 1, {
      method: "GET",
      path: "/foo",
    })) as { ok: boolean; path: string };
    expect(res).toEqual({ ok: true, path: "/foo" });
    expect(transport.requestCalls).toHaveLength(1);
    expect(transport.requestCalls[0].path).toBe("/foo");
  });

  it("rejects malformed payload with IpcValidationError", async () => {
    const { ipcMain } = setup();
    await expect(
      ipcMain.invoke(CHANNELS.transportRequest, 1, { method: "GET" }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });

  it("rejects a disallowed HTTP method with IpcValidationError", async () => {
    const { ipcMain } = setup();
    await expect(
      ipcMain.invoke(CHANNELS.transportRequest, 1, {
        method: "OPTIONS",
        path: "/foo",
      }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });

  it("rejects empty path with IpcValidationError", async () => {
    const { ipcMain } = setup();
    await expect(
      ipcMain.invoke(CHANNELS.transportRequest, 1, {
        method: "GET",
        path: "",
      }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });
});

describe("transport.subscribe / transport.unsubscribe", () => {
  it("subscribe opens an underlying transport subscription tagged by id", async () => {
    const { ipcMain, transport, bridge } = setup();
    await ipcMain.invoke(CHANNELS.transportSubscribe, 7, {
      subscriptionId: "sub-1",
      path: "/events",
    });
    expect(transport.subscriptions).toHaveLength(1);
    expect(bridge.activeSubscriptionCount()).toBe(1);
  });

  it("subscribe-then-emit forwards transport events to the right webContents", async () => {
    const { ipcMain, transport, events } = setup();
    await ipcMain.invoke(CHANNELS.transportSubscribe, 42, {
      subscriptionId: "sub-1",
      path: "/events",
    });
    transport.subscriptions[0].opts.onOpen?.();
    transport.subscriptions[0].opts.onMessage("hello");
    transport.subscriptions[0].opts.onError?.(new Error("oops"));
    expect(events).toEqual([
      {
        webContentsId: 42,
        payload: { subscriptionId: "sub-1", kind: "open" },
      },
      {
        webContentsId: 42,
        payload: { subscriptionId: "sub-1", kind: "message", message: "hello" },
      },
      {
        webContentsId: 42,
        payload: {
          subscriptionId: "sub-1",
          kind: "error",
          errorMessage: "oops",
        },
      },
    ]);
  });

  it("subscribe is synchronous in the handler body before the ack resolves", async () => {
    const { ipcMain, transport } = setup();
    // The invoke returns a promise; the bridge call inside is synchronous.
    // Verify transport.subscribeServerSentEvents was already called by the
    // time we await the promise.
    const ackPromise = ipcMain.invoke(CHANNELS.transportSubscribe, 1, {
      subscriptionId: "sub-1",
      path: "/events",
    });
    // Underlying transport subscribe has run synchronously.
    expect(transport.subscriptions).toHaveLength(1);
    await ackPromise;
  });

  it("emits a 'closed' stream-event after unsubscribe", async () => {
    const { ipcMain, transport, events } = setup();
    await ipcMain.invoke(CHANNELS.transportSubscribe, 3, {
      subscriptionId: "sub-1",
      path: "/events",
    });
    const res = (await ipcMain.invoke(CHANNELS.transportUnsubscribe, 3, {
      subscriptionId: "sub-1",
    })) as { removed: boolean };
    expect(res.removed).toBe(true);
    expect(transport.subscriptions[0].closed).toBe(true);
    expect(events.at(-1)?.payload).toEqual({
      subscriptionId: "sub-1",
      kind: "closed",
    });
  });

  it("unsubscribe of unknown id returns { removed: false }", async () => {
    const { ipcMain } = setup();
    const res = (await ipcMain.invoke(CHANNELS.transportUnsubscribe, 3, {
      subscriptionId: "ghost",
    })) as { removed: boolean };
    expect(res.removed).toBe(false);
  });

  it("rejects duplicate subscriptionId with an error and logs a warn", async () => {
    const { ipcMain, logger } = setup();
    await ipcMain.invoke(CHANNELS.transportSubscribe, 1, {
      subscriptionId: "sub-1",
      path: "/x",
    });
    await expect(
      ipcMain.invoke(CHANNELS.transportSubscribe, 1, {
        subscriptionId: "sub-1",
        path: "/x",
      }),
    ).rejects.toThrow(/already active/);
    expect(logger.warn).toHaveBeenCalledWith(
      "subscribe failed",
      expect.objectContaining({ subscriptionId: "sub-1" }),
    );
  });

  it("rejects malformed subscribe payload with IpcValidationError", async () => {
    const { ipcMain } = setup();
    await expect(
      ipcMain.invoke(CHANNELS.transportSubscribe, 1, {
        subscriptionId: "",
        path: "/x",
      }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });

  it("rejects malformed unsubscribe payload with IpcValidationError", async () => {
    const { ipcMain } = setup();
    await expect(
      ipcMain.invoke(CHANNELS.transportUnsubscribe, 1, {
        subscriptionId: "",
      }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });
});

describe("transport.session-snapshot", () => {
  it("returns the cached session + capabilities atomically", async () => {
    const { ipcMain } = setup();
    const snap = (await ipcMain.invoke(
      CHANNELS.transportSessionSnapshot,
      1,
      {},
    )) as { session: Session; capabilities: TransportCapabilities };
    expect(snap.session.bearer).toBeNull();
    expect(snap.capabilities.substrate).toBe("desktop-webview");
    expect(snap.capabilities.nativeSecretStorage).toBe(true);
  });

  it("accepts a missing payload (undefined → {})", async () => {
    const { ipcMain } = setup();
    const snap = (await ipcMain.invoke(
      CHANNELS.transportSessionSnapshot,
      1,
      undefined,
    )) as { session: Session };
    expect(snap.session.bearer).toBeNull();
  });

  it("rejects non-empty payload with IpcValidationError (strict empty)", async () => {
    const { ipcMain } = setup();
    await expect(
      ipcMain.invoke(CHANNELS.transportSessionSnapshot, 1, { extra: true }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });
});

describe("TransportBridge.unsubscribeForWebContents", () => {
  it("closes only that webContents' subscriptions", async () => {
    const { ipcMain, transport, bridge } = setup();
    await ipcMain.invoke(CHANNELS.transportSubscribe, 1, {
      subscriptionId: "sub-a",
      path: "/x",
    });
    await ipcMain.invoke(CHANNELS.transportSubscribe, 2, {
      subscriptionId: "sub-b",
      path: "/y",
    });
    expect(bridge.activeSubscriptionCount()).toBe(2);
    bridge.unsubscribeForWebContents(1);
    expect(bridge.activeSubscriptionCount()).toBe(1);
    expect(transport.subscriptions[0].closed).toBe(true);
    expect(transport.subscriptions[1].closed).toBe(false);
  });
});

describe("TransportBridge default — MockTransport when no transport supplied", () => {
  it("uses MockTransport for request / subscribe paths", async () => {
    const events: Array<{
      webContentsId: number;
      payload: StreamEventPayload;
    }> = [];
    const bridge = new TransportBridge((webContentsId, payload) =>
      events.push({ webContentsId, payload }),
    );
    // MockTransport's session is { bearer: null } and capabilities.substrate
    // is "web" — desktop-webview is the IpcTransport bootstrap value, not
    // the bridge's. This test asserts the default wiring uses MockTransport.
    const snap = bridge.sessionSnapshot();
    expect(snap.session.bearer).toBeNull();
    expect(snap.capabilities.substrate).toBe("web");
  });
});

describe("auth.* channels", () => {
  function setupWithAuth() {
    const ipcMain = makeFakeIpcMain();
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, { transport });
    type RS = {
      workspaceId: string;
      expiresAt: number;
      displayName: string | null;
      email: string | null;
    };
    const auth = {
      signIn: vi.fn(
        async (workspaceId: string): Promise<RS> => ({
          workspaceId,
          expiresAt: Date.now() + 60_000,
          displayName: "Sarah",
          email: "sarah@acme.test",
        }),
      ),
      signOut: vi.fn(async (_workspaceId: string): Promise<void> => {}),
      getSession: vi.fn(
        async (_workspaceId: string): Promise<RS | null> => null,
      ),
      refresh: vi.fn(async (_workspaceId: string): Promise<RS | null> => null),
    };
    const teardown = registerIpcHandlers({
      ipcMain: ipcMain as unknown as Parameters<
        typeof registerIpcHandlers
      >[0]["ipcMain"],
      bridge,
      auth,
    });
    return { ipcMain, auth, teardown };
  }

  it("registers all four auth channels", () => {
    const { ipcMain } = setupWithAuth();
    expect(ipcMain.has(CHANNELS.authSignIn)).toBe(true);
    expect(ipcMain.has(CHANNELS.authSignOut)).toBe(true);
    expect(ipcMain.has(CHANNELS.authRefresh)).toBe(true);
    expect(ipcMain.has(CHANNELS.authGetSession)).toBe(true);
  });

  it("auth.sign-in forwards the workspaceId and returns the RendererSession", async () => {
    const { ipcMain, auth } = setupWithAuth();
    const res = (await ipcMain.invoke(CHANNELS.authSignIn, 1, {
      workspaceId: "org_acme",
    })) as { workspaceId: string };
    expect(res.workspaceId).toBe("org_acme");
    expect(auth.signIn).toHaveBeenCalledWith("org_acme");
  });

  it("auth.sign-out forwards the workspaceId and returns ok", async () => {
    const { ipcMain, auth } = setupWithAuth();
    const res = await ipcMain.invoke(CHANNELS.authSignOut, 1, {
      workspaceId: "org_acme",
    });
    expect(res).toEqual({ ok: true });
    expect(auth.signOut).toHaveBeenCalledWith("org_acme");
  });

  it("auth.refresh forwards the workspaceId", async () => {
    const { ipcMain, auth } = setupWithAuth();
    await ipcMain.invoke(CHANNELS.authRefresh, 1, { workspaceId: "org_acme" });
    expect(auth.refresh).toHaveBeenCalledWith("org_acme");
  });

  it("auth.get-session forwards the workspaceId", async () => {
    const { ipcMain, auth } = setupWithAuth();
    await ipcMain.invoke(CHANNELS.authGetSession, 1, {
      workspaceId: "org_acme",
    });
    expect(auth.getSession).toHaveBeenCalledWith("org_acme");
  });

  it("rejects auth payloads without a workspaceId", async () => {
    const { ipcMain } = setupWithAuth();
    await expect(
      ipcMain.invoke(CHANNELS.authSignIn, 1, {}),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });

  it("teardown removes auth handlers when an auth service was provided", () => {
    const { ipcMain, teardown } = setupWithAuth();
    teardown();
    expect(ipcMain.has(CHANNELS.authSignIn)).toBe(false);
    expect(ipcMain.has(CHANNELS.authSignOut)).toBe(false);
    expect(ipcMain.has(CHANNELS.authRefresh)).toBe(false);
    expect(ipcMain.has(CHANNELS.authGetSession)).toBe(false);
  });
});

describe("TransportBridge bearerProvider", () => {
  it("attaches Authorization: Bearer header to outbound requests", async () => {
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, {
      transport,
      bearerProvider: async () => "tok-1",
    });
    await bridge.request({ method: "GET", path: "/foo" });
    expect(transport.requestCalls[0].headers?.authorization).toBe(
      "Bearer tok-1",
    );
  });

  it("makes a no-auth request when the provider returns null", async () => {
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, {
      transport,
      bearerProvider: async () => null,
    });
    await bridge.request({ method: "GET", path: "/foo" });
    expect(transport.requestCalls[0].headers?.authorization).toBeUndefined();
  });
});

describe("tier2.boundary-error channel (Phase 6C)", () => {
  it("registers the handler only when tier2 dispatcher is supplied", () => {
    const ipcMain = makeFakeIpcMain();
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, { transport });
    registerIpcHandlers({
      ipcMain: ipcMain as unknown as Parameters<
        typeof registerIpcHandlers
      >[0]["ipcMain"],
      bridge,
    });
    expect(ipcMain.has(CHANNELS.tier2BoundaryError)).toBe(false);
  });

  it("validates and forwards to the dispatcher", async () => {
    const ipcMain = makeFakeIpcMain();
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, { transport });
    const onBoundaryError = vi.fn();
    registerIpcHandlers({
      ipcMain: ipcMain as unknown as Parameters<
        typeof registerIpcHandlers
      >[0]["ipcMain"],
      bridge,
      tier2: { onBoundaryError },
    });
    const result = (await ipcMain.invoke(CHANNELS.tier2BoundaryError, 42, {
      scheme: "email",
      version: 1,
      method: "renderCurrent",
      message: "TypeError: x is undefined",
    })) as { ok: true };
    expect(result.ok).toBe(true);
    expect(onBoundaryError).toHaveBeenCalledWith({
      scheme: "email",
      version: 1,
      method: "renderCurrent",
      message: "TypeError: x is undefined",
    });
  });

  it("rejects malformed payloads with IpcValidationError", async () => {
    const ipcMain = makeFakeIpcMain();
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, { transport });
    registerIpcHandlers({
      ipcMain: ipcMain as unknown as Parameters<
        typeof registerIpcHandlers
      >[0]["ipcMain"],
      bridge,
      tier2: { onBoundaryError: vi.fn() },
    });
    await expect(
      ipcMain.invoke(CHANNELS.tier2BoundaryError, 1, {
        scheme: "email",
        version: "not-a-number",
        method: "renderCurrent",
        message: "x",
      }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });

  it("rejects an unknown method enum value", async () => {
    const ipcMain = makeFakeIpcMain();
    const transport = new FakeTransport();
    const bridge = new TransportBridge(() => undefined, { transport });
    registerIpcHandlers({
      ipcMain: ipcMain as unknown as Parameters<
        typeof registerIpcHandlers
      >[0]["ipcMain"],
      bridge,
      tier2: { onBoundaryError: vi.fn() },
    });
    await expect(
      ipcMain.invoke(CHANNELS.tier2BoundaryError, 1, {
        scheme: "email",
        version: 1,
        method: "renderUnknown",
        message: "x",
      }),
    ).rejects.toBeInstanceOf(IpcValidationError);
  });
});
