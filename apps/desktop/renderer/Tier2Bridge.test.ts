// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearRegistry, resolveAdapter } from "@0x-copilot/chat-surface";
import { CHANNELS, type WindowBridge } from "@0x-copilot/chat-transport";

import { Tier2Bridge } from "./Tier2Bridge";

interface FakeBridge {
  bridge: WindowBridge;
  handlers: Map<string, (raw: unknown) => void>;
  invokeCalls: Array<{ channel: string; payload: unknown }>;
}

function makeBridge(): FakeBridge {
  const handlers = new Map<string, (raw: unknown) => void>();
  const invokeCalls: Array<{ channel: string; payload: unknown }> = [];
  const bridge: WindowBridge = {
    ipc: {
      invoke: <T>(channel: string, payload?: unknown): Promise<T> => {
        invokeCalls.push({ channel, payload });
        return Promise.resolve(null as unknown as T);
      },
      on: (channel, handler) => {
        handlers.set(channel, handler);
        return () => {
          if (handlers.get(channel) === handler) {
            handlers.delete(channel);
          }
        };
      },
    },
  };
  return { bridge, handlers, invokeCalls };
}

beforeEach(() => {
  clearRegistry();
});

describe("Tier2Bridge — install / uninstall / mark-broken", () => {
  it("registers the listeners on attach", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    expect(handlers.has(CHANNELS.tier2Install)).toBe(true);
    expect(handlers.has(CHANNELS.tier2Uninstall)).toBe(true);
    expect(handlers.has(CHANNELS.tier2MarkBroken)).toBe(true);
  });

  it("on tier2.install, registerAdapter is called and resolveAdapter returns the adapter", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 7,
      source: "module.exports={};",
      generatedAt: "2026-05-17T00:00:00Z",
      generatorModel: "render-adapter-generator/v1",
    });
    const adapter = resolveAdapter("email://draft-7");
    expect(adapter).not.toBeNull();
    expect(adapter?.scheme).toBe("email");
    expect(adapter?.metadata.schemaVersion).toBe(7);
    expect(adapter?.metadata.origin).toBe("agent-generated");
  });

  it("ignores malformed install payloads (Zod safeParse rejects)", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: "not-a-number",
      source: "x",
      generatedAt: "x",
      generatorModel: "x",
    });
    expect(resolveAdapter("email://draft-7")).toBeNull();
  });

  it("on tier2.uninstall, the adapter is removed from the registry", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 1,
      source: "module.exports={};",
      generatedAt: "2026-05-17T00:00:00Z",
      generatorModel: "x",
    });
    expect(resolveAdapter("email://x")).not.toBeNull();
    handlers.get(CHANNELS.tier2Uninstall)?.({
      scheme: "email",
      version: 1,
    });
    expect(resolveAdapter("email://x")).toBeNull();
  });

  it("on tier2.mark-broken, resolveAdapter returns null (broken entries are skipped)", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 1,
      source: "module.exports={};",
      generatedAt: "2026-05-17T00:00:00Z",
      generatorModel: "x",
    });
    expect(resolveAdapter("email://x")).not.toBeNull();
    handlers.get(CHANNELS.tier2MarkBroken)?.({
      scheme: "email",
      version: 1,
      method: "renderCurrent",
      reason: "TypeError",
    });
    expect(resolveAdapter("email://x")).toBeNull();
  });

  it("detach removes the handlers", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    const detach = t2.attach();
    expect(handlers.size).toBe(3);
    detach();
    expect(handlers.size).toBe(0);
  });

  it("the installed adapter mounts <Tier2Loader> when called", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 1,
      source: "module.exports={};",
      generatedAt: "2026-05-17T00:00:00Z",
      generatorModel: "x",
    });
    const adapter = resolveAdapter("email://draft-1");
    expect(adapter).not.toBeNull();
    const element = adapter!.renderCurrent({ id: "draft-1" });
    expect(element).not.toBeNull();
    // renderCurrent returns a React element whose type is the Tier2Loader
    // function component.
    const renderedType = (element as unknown as { type: unknown }).type;
    expect(typeof renderedType).toBe("function");
  });

  it("matches the scheme strictly", () => {
    const { bridge, handlers } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 1,
      source: "module.exports={};",
      generatedAt: "x",
      generatorModel: "x",
    });
    expect(resolveAdapter("slack://x")).toBeNull();
    expect(resolveAdapter("nothing")).toBeNull();
    expect(resolveAdapter("email://anything")).not.toBeNull();
  });
});

describe("Tier2Bridge — worker factory wiring (PRD-10)", () => {
  it("threads the provided workerFactory into the mounted Tier2Loader", () => {
    const { bridge, handlers } = makeBridge();
    const workerFactory = () => {
      throw new Error("factory should only be invoked by the loader effect");
    };
    const t2 = new Tier2Bridge({ bridge, workerFactory });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 1,
      source: "module.exports={};",
      generatedAt: "x",
      generatorModel: "x",
    });
    const adapter = resolveAdapter("email://draft-1");
    const element = adapter!.renderCurrent({ id: "x" }) as unknown as {
      props: { workerFactory?: unknown };
    };
    expect(element.props.workerFactory).toBe(workerFactory);
  });
});

describe("Tier2Bridge — boundary error forwarding", () => {
  it("on Tier2Loader.onFailure, sends tier2.boundary-error back to main", () => {
    const { bridge, handlers, invokeCalls } = makeBridge();
    const t2 = new Tier2Bridge({ bridge });
    t2.attach();
    handlers.get(CHANNELS.tier2Install)?.({
      scheme: "email",
      version: 1,
      source: "module.exports={};",
      generatedAt: "x",
      generatorModel: "x",
    });
    const adapter = resolveAdapter("email://draft-1");
    const element = adapter!.renderCurrent({ id: "x" }) as unknown as {
      props: {
        onFailure: (reason: string, detail?: string) => void;
      };
    };
    element.props.onFailure("throw", "TypeError: x is undefined");
    expect(invokeCalls).toEqual([
      {
        channel: CHANNELS.tier2BoundaryError,
        payload: {
          scheme: "email",
          version: 1,
          method: "renderCurrent",
          message: "throw: TypeError: x is undefined",
        },
      },
    ]);
  });
});
