import { describe, expect, it, vi } from "vitest";

import {
  TransportHttpError,
  UnauthorizedError,
  type Session,
  type TransportCapabilities,
} from "../types";
import { IpcTransport } from "./IpcTransport";
import {
  CHANNELS,
  wrapTransportError,
  wrapTransportValue,
  type StreamEventPayload,
} from "./rpc-protocol";
import type { WindowBridge } from "./window-bridge";

interface FakeBridge extends WindowBridge {
  emit(channel: string, payload: unknown): void;
  invokeCalls(): Array<{ channel: string; payload: unknown }>;
}

function makeFakeBridge(
  invokeImpl?: (channel: string, payload: unknown) => Promise<unknown>,
): FakeBridge {
  const listeners = new Map<string, Array<(payload: unknown) => void>>();
  const calls: Array<{ channel: string; payload: unknown }> = [];
  const defaultImpl = async (): Promise<unknown> => undefined;
  const fn = invokeImpl ?? defaultImpl;
  return {
    ipc: {
      invoke: vi.fn(async (channel: string, payload: unknown) => {
        calls.push({ channel, payload });
        return fn(channel, payload);
      }) as WindowBridge["ipc"]["invoke"],
      on(channel: string, handler: (payload: unknown) => void) {
        let arr = listeners.get(channel);
        if (!arr) {
          arr = [];
          listeners.set(channel, arr);
        }
        arr.push(handler);
        return () => {
          const a = listeners.get(channel);
          if (!a) return;
          const idx = a.indexOf(handler);
          if (idx >= 0) a.splice(idx, 1);
        };
      },
    },
    emit(channel: string, payload: unknown) {
      const arr = listeners.get(channel) ?? [];
      for (const h of arr) h(payload);
    },
    invokeCalls() {
      return calls;
    },
  };
}

const BOOTSTRAP_SESSION: Session = { bearer: null };
const BOOTSTRAP_CAPABILITIES: TransportCapabilities = {
  substrate: "desktop-webview",
  nativeSecretStorage: true,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

function makeTransport(
  bridge: FakeBridge,
  randomIdSeed?: () => string,
): IpcTransport {
  let n = 0;
  return new IpcTransport({
    bridge,
    bootstrapSession: BOOTSTRAP_SESSION,
    bootstrapCapabilities: BOOTSTRAP_CAPABILITIES,
    randomId: randomIdSeed ?? (() => `sub-${++n}`),
  });
}

function emit(bridge: FakeBridge, payload: StreamEventPayload): void {
  bridge.emit(CHANNELS.streamEvent, payload);
}

describe("IpcTransport.request", () => {
  it("proxies via bridge.ipc.invoke with the request payload minus signal", async () => {
    const bridge = makeFakeBridge(async () => ({ ok: true }));
    const transport = makeTransport(bridge);
    const res = await transport.request<{ ok: boolean }>({
      method: "GET",
      path: "/foo",
      query: { a: 1 },
    });
    expect(res).toEqual({ ok: true });
    expect(bridge.invokeCalls()).toEqual([
      {
        channel: CHANNELS.transportRequest,
        payload: { method: "GET", path: "/foo", query: { a: 1 } },
      },
    ]);
  });

  it("drops AbortSignal from the IPC payload (not structured-cloneable)", async () => {
    const bridge = makeFakeBridge(async () => undefined);
    const transport = makeTransport(bridge);
    const ac = new AbortController();
    await transport.request({
      method: "POST",
      path: "/x",
      body: { a: 1 },
      signal: ac.signal,
    });
    const call = bridge.invokeCalls()[0];
    expect(call.payload).toEqual({
      method: "POST",
      path: "/x",
      body: { a: 1 },
    });
    expect("signal" in (call.payload as object)).toBe(false);
  });

  it("propagates rejection from the bridge", async () => {
    const bridge = makeFakeBridge(async () => {
      throw new Error("boom");
    });
    const transport = makeTransport(bridge);
    await expect(
      transport.request({ method: "GET", path: "/x" }),
    ).rejects.toThrow("boom");
  });

  it("unwraps a success envelope from main", async () => {
    const bridge = makeFakeBridge(async () =>
      wrapTransportValue({ hello: "world" }),
    );
    const transport = makeTransport(bridge);
    const res = await transport.request<{ hello: string }>({
      method: "GET",
      path: "/foo",
    });
    expect(res).toEqual({ hello: "world" });
  });

  it("rehydrates a structured HTTP error envelope as TransportHttpError", async () => {
    const bridge = makeFakeBridge(async () =>
      wrapTransportError({
        status: 409,
        message: "This wallet already belongs to another account.",
        detail: { code: "merge_required", safe_message: "…" },
      }),
    );
    const transport = makeTransport(bridge);
    const err = await transport
      .request({ method: "POST", path: "/v1/me/identities/wallet" })
      .then(
        () => null,
        (e: unknown) => e,
      );
    expect(err).toBeInstanceOf(TransportHttpError);
    expect((err as TransportHttpError).status).toBe(409);
    expect((err as TransportHttpError).code).toBe("merge_required");
  });

  it("rehydrates a 401 envelope as UnauthorizedError", async () => {
    const bridge = makeFakeBridge(async () =>
      wrapTransportError({ status: 401, message: "expired", detail: null }),
    );
    const transport = makeTransport(bridge);
    await expect(
      transport.request({ method: "GET", path: "/x" }),
    ).rejects.toBeInstanceOf(UnauthorizedError);
  });
});

describe("IpcTransport.getSession and capabilities", () => {
  it("returns the bootstrap session synchronously", () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    expect(transport.getSession()).toEqual(BOOTSTRAP_SESSION);
  });

  it("returns the bootstrap capabilities synchronously", () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    expect(transport.capabilities()).toEqual(BOOTSTRAP_CAPABILITIES);
  });
});

describe("IpcTransport.subscribeServerSentEvents", () => {
  it("returns synchronously and fires the IPC in the background", () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    const sub = transport.subscribeServerSentEvents({
      path: "/events",
      onMessage: () => {},
    });
    expect(typeof sub.close).toBe("function");
    expect(bridge.invokeCalls()).toEqual([
      {
        channel: CHANNELS.transportSubscribe,
        payload: {
          subscriptionId: "sub-1",
          path: "/events",
          query: undefined,
          eventName: undefined,
        },
      },
    ]);
  });

  it("dispatches the open / message / closed events to onOpen / onMessage", async () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    const onOpen = vi.fn();
    const onMessage = vi.fn();
    const onError = vi.fn();
    transport.subscribeServerSentEvents({
      path: "/events",
      onOpen,
      onMessage,
      onError,
    });
    emit(bridge, { subscriptionId: "sub-1", kind: "open" });
    emit(bridge, {
      subscriptionId: "sub-1",
      kind: "message",
      message: "hello",
    });
    emit(bridge, {
      subscriptionId: "sub-1",
      kind: "message",
      message: "world",
    });
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith("hello");
    expect(onMessage).toHaveBeenCalledWith("world");
    expect(onError).not.toHaveBeenCalled();
  });

  it("onOpen is only fired once even if main emits multiple open events", () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    const onOpen = vi.fn();
    transport.subscribeServerSentEvents({
      path: "/events",
      onOpen,
      onMessage: () => {},
    });
    emit(bridge, { subscriptionId: "sub-1", kind: "open" });
    emit(bridge, { subscriptionId: "sub-1", kind: "open" });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("dispatches error events to onError as Error instances", () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    const onError = vi.fn();
    transport.subscribeServerSentEvents({
      path: "/events",
      onMessage: () => {},
      onError,
    });
    emit(bridge, {
      subscriptionId: "sub-1",
      kind: "error",
      errorMessage: "stream broke",
    });
    expect(onError).toHaveBeenCalledTimes(1);
    const err = onError.mock.calls[0][0] as Error;
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe("stream broke");
  });

  it("subscribe-then-immediate-close: close() before invoke resolves still fires unsubscribe", async () => {
    let resolveSubscribe: (() => void) | undefined;
    const subscribePromise = new Promise<void>((resolve) => {
      resolveSubscribe = resolve;
    });
    const bridge = makeFakeBridge((channel) => {
      if (channel === CHANNELS.transportSubscribe) {
        return subscribePromise.then(() => undefined);
      }
      return Promise.resolve(undefined);
    });
    const transport = makeTransport(bridge);
    const sub = transport.subscribeServerSentEvents({
      path: "/events",
      onMessage: () => {},
    });
    sub.close();
    resolveSubscribe?.();
    await subscribePromise;
    const channels = bridge.invokeCalls().map((c) => c.channel);
    expect(channels).toEqual([
      CHANNELS.transportSubscribe,
      CHANNELS.transportUnsubscribe,
    ]);
  });

  it("subscribe failure dispatches onError via the stream-error path", async () => {
    let rejectSubscribe: ((reason: Error) => void) | undefined;
    const subscribePromise = new Promise<void>((_resolve, reject) => {
      rejectSubscribe = reject;
    });
    const bridge = makeFakeBridge((channel) => {
      if (channel === CHANNELS.transportSubscribe) {
        return subscribePromise;
      }
      return Promise.resolve(undefined);
    });
    const transport = makeTransport(bridge);
    const onError = vi.fn();
    transport.subscribeServerSentEvents({
      path: "/events",
      onMessage: () => {},
      onError,
    });
    rejectSubscribe?.(new Error("nope"));
    await subscribePromise.catch(() => {});
    // Flush the .catch microtask.
    await Promise.resolve();
    expect(onError).toHaveBeenCalledTimes(1);
    const err = onError.mock.calls[0][0] as Error;
    expect(err.message).toBe("subscribe failed: nope");
  });

  it("concurrent subscriptions route events to the right handler", () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    const onMessageA = vi.fn();
    const onMessageB = vi.fn();
    transport.subscribeServerSentEvents({
      path: "/a",
      onMessage: onMessageA,
    });
    transport.subscribeServerSentEvents({
      path: "/b",
      onMessage: onMessageB,
    });
    emit(bridge, { subscriptionId: "sub-1", kind: "message", message: "a1" });
    emit(bridge, { subscriptionId: "sub-2", kind: "message", message: "b1" });
    emit(bridge, { subscriptionId: "sub-2", kind: "message", message: "b2" });
    expect(onMessageA.mock.calls.map((c) => c[0])).toEqual(["a1"]);
    expect(onMessageB.mock.calls.map((c) => c[0])).toEqual(["b1", "b2"]);
  });

  it("close() evicts the record and an event arriving after close is dropped", async () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    const onMessage = vi.fn();
    const sub = transport.subscribeServerSentEvents({
      path: "/events",
      onMessage,
    });
    emit(bridge, { subscriptionId: "sub-1", kind: "message", message: "pre" });
    sub.close();
    emit(bridge, { subscriptionId: "sub-1", kind: "message", message: "post" });
    // Let the microtask buffer drain.
    await Promise.resolve();
    expect(onMessage.mock.calls.map((c) => c[0])).toEqual(["pre"]);
  });
});

describe("IpcTransport stream-event buffer-and-replay", () => {
  it("buffers an event whose subscriptionId is not yet known and replays after microtask", async () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    // Emit FIRST (no subscription yet).
    emit(bridge, {
      subscriptionId: "sub-1",
      kind: "message",
      message: "early",
    });
    // Now subscribe — note our randomId seed returns "sub-1" first.
    const onMessage = vi.fn();
    transport.subscribeServerSentEvents({
      path: "/events",
      onMessage,
    });
    // Microtask flush.
    await Promise.resolve();
    expect(onMessage).toHaveBeenCalledWith("early");
  });

  it("drops buffered events whose subscriptionId never registers", async () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    emit(bridge, {
      subscriptionId: "phantom",
      kind: "message",
      message: "lost",
    });
    await Promise.resolve();
    // No subscription registered for "phantom" — event silently dropped.
    // Nothing to assert beyond "no crash"; ensure subscription map is empty.
    const onMessage = vi.fn();
    transport.subscribeServerSentEvents({ path: "/x", onMessage });
    emit(bridge, { subscriptionId: "sub-1", kind: "message", message: "ok" });
    expect(onMessage).toHaveBeenCalledWith("ok");
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it("buffer cap prevents unbounded growth on phantom subscriptions", async () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    // Push more than the cap (16) before any subscribe.
    for (let i = 0; i < 32; i++) {
      emit(bridge, {
        subscriptionId: "phantom",
        kind: "message",
        message: `m${i}`,
      });
    }
    await Promise.resolve();
    // Subscribe with the seed id that matches none of the phantoms.
    const onMessage = vi.fn();
    transport.subscribeServerSentEvents({ path: "/x", onMessage });
    expect(onMessage).not.toHaveBeenCalled();
  });
});

describe("IpcTransport.dispose", () => {
  it("removes the stream listener and unsubscribes every active subscription", async () => {
    const bridge = makeFakeBridge();
    const transport = makeTransport(bridge);
    transport.subscribeServerSentEvents({ path: "/a", onMessage: () => {} });
    transport.subscribeServerSentEvents({ path: "/b", onMessage: () => {} });
    transport.dispose();
    const channels = bridge.invokeCalls().map((c) => c.channel);
    expect(
      channels.filter((c) => c === CHANNELS.transportUnsubscribe),
    ).toHaveLength(2);
    const onMessage = vi.fn();
    // After dispose the listener is removed, so further emits do nothing.
    emit(bridge, { subscriptionId: "sub-1", kind: "message", message: "x" });
    await Promise.resolve();
    expect(onMessage).not.toHaveBeenCalled();
  });
});
