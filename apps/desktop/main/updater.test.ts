// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { UpdateStatusPayload } from "@0x-copilot/chat-transport";

import {
  initAutoUpdate,
  type AutoUpdaterLike,
  type AutoUpdateDeps,
} from "./updater";

type Listener = (arg?: unknown) => void;

// Records event wiring + lets tests fire electron-updater lifecycle events.
// Cast to AutoUpdaterLike at the boundary (its `on` is a strict overload set).
class FakeAutoUpdater {
  autoDownload = false;
  autoInstallOnAppQuit = false;
  readonly listeners = new Map<string, Listener>();
  checkForUpdates = vi.fn(() => Promise.resolve({}));

  on(event: string, listener: Listener): this {
    this.listeners.set(event, listener);
    return this;
  }

  fire(event: string, arg?: unknown): void {
    this.listeners.get(event)?.(arg);
  }
}

function harness(overrides: Partial<AutoUpdateDeps> = {}): {
  updater: FakeAutoUpdater;
  emitted: UpdateStatusPayload[];
  intervals: Array<{ fn: () => void; ms: number; unref: () => void }>;
  deps: AutoUpdateDeps;
} {
  const updater = new FakeAutoUpdater();
  const emitted: UpdateStatusPayload[] = [];
  const intervals: Array<{ fn: () => void; ms: number; unref: () => void }> =
    [];
  const deps: AutoUpdateDeps = {
    autoUpdater: updater as unknown as AutoUpdaterLike,
    isPackaged: true,
    hasUpdateConfig: true,
    emit: (status) => emitted.push(status),
    setInterval: (fn, ms) => {
      const entry = { fn, ms, unref: vi.fn() };
      intervals.push(entry);
      return { unref: entry.unref };
    },
    clearInterval: vi.fn(),
    ...overrides,
  };
  return { updater, emitted, intervals, deps };
}

describe("initAutoUpdate — dev/unsigned guard", () => {
  it("no-ops when the build is not packaged", () => {
    const { updater, deps } = harness({ isPackaged: false });
    const handle = initAutoUpdate(deps);
    expect(updater.checkForUpdates).not.toHaveBeenCalled();
    // No event wiring happened.
    expect(updater.listeners.size).toBe(0);
    // Handle is safe to call.
    handle.stop();
  });

  it("no-ops when update metadata is absent (bare --dir build)", () => {
    const { updater, deps } = harness({ hasUpdateConfig: false });
    initAutoUpdate(deps);
    expect(updater.checkForUpdates).not.toHaveBeenCalled();
    expect(updater.listeners.size).toBe(0);
  });
});

describe("initAutoUpdate — active", () => {
  it("configures background download + install-on-quit only", () => {
    const { updater, deps } = harness();
    initAutoUpdate(deps);
    expect(updater.autoDownload).toBe(true);
    expect(updater.autoInstallOnAppQuit).toBe(true);
  });

  it("checks immediately and schedules a 4h recheck", async () => {
    const { updater, intervals, deps } = harness();
    initAutoUpdate(deps);
    // First check is fired on the microtask queue.
    await Promise.resolve();
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(1);
    expect(intervals).toHaveLength(1);
    expect(intervals[0]!.ms).toBe(4 * 60 * 60 * 1000);
    // The scheduled tick triggers another check.
    intervals[0]!.fn();
    await Promise.resolve();
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(2);
  });

  it("relays the electron-updater lifecycle to emit()", () => {
    const { updater, emitted, deps } = harness();
    initAutoUpdate(deps);
    updater.fire("checking-for-update");
    updater.fire("update-available", { version: "0.3.0" });
    updater.fire("update-downloaded", { version: "0.3.0" });
    updater.fire("update-not-available", { version: "0.2.0" });
    expect(emitted).toEqual([
      { kind: "checking" },
      { kind: "available", version: "0.3.0" },
      { kind: "downloaded", version: "0.3.0" },
      { kind: "not-available" },
    ]);
  });

  it("surfaces autoUpdater 'error' events without throwing", () => {
    const { updater, emitted, deps } = harness();
    initAutoUpdate(deps);
    updater.fire("error", new Error("code signature invalid"));
    expect(emitted).toContainEqual({
      kind: "error",
      message: "code signature invalid",
    });
  });

  it("emits an error (never rejects) when checkForUpdates throws", async () => {
    const { updater, emitted, deps } = harness();
    updater.checkForUpdates.mockRejectedValueOnce(new Error("offline"));
    const handle = initAutoUpdate(deps);
    await handle.checkNow();
    expect(emitted).toContainEqual({ kind: "error", message: "offline" });
  });

  it("stop() clears the interval", () => {
    const { deps } = harness();
    const clearSpy = deps.clearInterval as ReturnType<typeof vi.fn>;
    const handle = initAutoUpdate(deps);
    handle.stop();
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });
});
