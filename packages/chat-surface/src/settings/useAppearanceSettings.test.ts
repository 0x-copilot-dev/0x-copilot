// useAppearanceSettings — D9 boot-load + persist controller (PRD-12 DoD 22/23).
//
// These tests FAIL on `main`: there is no boot read there, so DoD 22's
// "onApply once on mount" cannot pass.

import {
  Session,
  type Transport,
  type TransportCapabilities,
  type TypedRequest,
} from "@0x-copilot/chat-transport";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { KeyValueStore } from "../storage/key-value-store";

import {
  useAppearanceSettings,
  type AppearanceSettingsPorts,
} from "./useAppearanceSettings";

const CAPS: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

function makeKv(): KeyValueStore & { set: ReturnType<typeof vi.fn> } {
  const set = vi.fn();
  return {
    get: () => null,
    set,
    keys: () => [],
  };
}

function makePorts(
  request: Transport["request"],
  kv: KeyValueStore,
  onApply = vi.fn(),
): AppearanceSettingsPorts & { onApply: ReturnType<typeof vi.fn> } {
  const transport: Transport = {
    request,
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: () => CAPS,
  };
  return { transport, keyValueStore: kv, onApply };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

const BOOT_PREFS = {
  appearance: {
    theme: "dark",
    accent: "violet",
    density: "compact",
    reduce_motion: "auto",
  },
};

describe("useAppearanceSettings", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("boot-loads /v1/me/preferences and paints attributes ONCE on mount, before any interaction (DoD 22)", async () => {
    const request = vi.fn((req: TypedRequest) =>
      req.method === "GET" ? Promise.resolve(BOOT_PREFS) : Promise.resolve({}),
    );
    const ports = makePorts(
      request as unknown as Transport["request"],
      makeKv(),
    );
    renderHook(() => useAppearanceSettings(ports));
    await flush();

    expect(ports.onApply).toHaveBeenCalledTimes(1);
    expect(ports.onApply).toHaveBeenCalledWith({
      "data-theme": "dark",
      "data-accent": "violet",
      "data-density": "compact",
      "data-reduce-motion": "auto",
    });
  });

  it("PUTs a contract accent once after the 300ms debounce and never writes KeyValueStore (DoD 23)", async () => {
    const request = vi.fn((req: TypedRequest) =>
      req.method === "GET" ? Promise.resolve(BOOT_PREFS) : Promise.resolve({}),
    );
    const kv = makeKv();
    const ports = makePorts(request as unknown as Transport["request"], kv);
    const { result } = renderHook(() => useAppearanceSettings(ports));
    await flush();

    act(() => {
      result.current.change({ accent: "violet" });
    });
    // Debounced: nothing PUT yet.
    expect(
      request.mock.calls.filter((c) => c[0].method === "PUT"),
    ).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(300);
    });
    await flush();

    const puts = request.mock.calls.filter((c) => c[0].method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0][0].path).toBe("/v1/me/preferences");
    expect(puts[0][0].body).toEqual({ appearance: { accent: "violet" } });
    // A contract accent is server-persisted; the KV shadow is never written.
    expect(kv.set).not.toHaveBeenCalled();
  });

  it("routes an OFF-contract accent to KeyValueStore with NO PUT (DoD 23)", async () => {
    const request = vi.fn((req: TypedRequest) =>
      req.method === "GET" ? Promise.resolve(BOOT_PREFS) : Promise.resolve({}),
    );
    const kv = makeKv();
    const ports = makePorts(request as unknown as Transport["request"], kv);
    const { result } = renderHook(() => useAppearanceSettings(ports));
    await flush();

    act(() => {
      // `ember` is not in ACCENT_SCHEMES → the FR-5.9a KeyValueStore fallback.
      result.current.change({ accent: "ember" });
    });
    expect(kv.set).toHaveBeenCalledTimes(1);
    expect(kv.set.mock.calls[0][0]).toBe("chat-surface.appearance.local");
    expect(JSON.parse(kv.set.mock.calls[0][1] as string).accent).toBe("ember");

    act(() => {
      vi.advanceTimersByTime(300);
    });
    await flush();
    expect(
      request.mock.calls.filter((c) => c[0].method === "PUT"),
    ).toHaveLength(0);
  });

  it("keeps the optimistic value and sets error on a rejected PUT (DoD 23)", async () => {
    const request = vi.fn((req: TypedRequest) =>
      req.method === "GET"
        ? Promise.resolve(BOOT_PREFS)
        : Promise.reject(new Error("save failed")),
    );
    const ports = makePorts(
      request as unknown as Transport["request"],
      makeKv(),
    );
    const { result } = renderHook(() => useAppearanceSettings(ports));
    await flush();

    act(() => {
      result.current.change({ accent: "sky" });
    });
    // Optimistic paint applied immediately, before the (failing) save.
    expect(result.current.value.accent).toBe("sky");

    act(() => {
      vi.advanceTimersByTime(300);
    });
    await flush();

    // The failed PUT does NOT undo the user's click; the error surfaces.
    expect(result.current.value.accent).toBe("sky");
    expect(result.current.error).not.toBeNull();
  });

  it("overlays an off-contract KeyValueStore value on top of the server snapshot at boot", async () => {
    const request = vi.fn((req: TypedRequest) =>
      req.method === "GET" ? Promise.resolve(BOOT_PREFS) : Promise.resolve({}),
    );
    const set = vi.fn();
    const kv: KeyValueStore & { set: ReturnType<typeof vi.fn> } = {
      get: (key) =>
        key === "chat-surface.appearance.local"
          ? JSON.stringify({ accent: "ember" })
          : null,
      set,
      keys: () => [],
    };
    const ports = makePorts(request as unknown as Transport["request"], kv);
    const { result } = renderHook(() => useAppearanceSettings(ports));
    await flush();
    // The server said violet, but the KV overlay (off-contract ember) wins.
    expect(result.current.value.accent).toBe("ember");
    expect(ports.onApply).toHaveBeenCalledWith(
      expect.objectContaining({ "data-accent": "ember" }),
    );
  });
});
