// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import {
  installSingleInstance,
  shouldSupervise,
  type SingleInstanceAppLike,
} from "./boot-mode";

describe("shouldSupervise", () => {
  it("supervises when packaged", () => {
    expect(shouldSupervise({ isPackaged: true, env: {} })).toBe(true);
  });

  it("supervises in dev when ATLAS_RUNTIME_DIR points at a staged runtime", () => {
    expect(
      shouldSupervise({
        isPackaged: false,
        env: { ATLAS_RUNTIME_DIR: "/repo/apps/desktop/resources" },
      }),
    ).toBe(true);
  });

  it("does NOT supervise plain dev (ATLAS_FACADE_URL flow unchanged)", () => {
    expect(
      shouldSupervise({
        isPackaged: false,
        env: { ATLAS_FACADE_URL: "http://127.0.0.1:8200" },
      }),
    ).toBe(false);
    expect(
      shouldSupervise({ isPackaged: false, env: { ATLAS_RUNTIME_DIR: "" } }),
    ).toBe(false);
  });
});

describe("installSingleInstance", () => {
  function makeApp(gotLock: boolean): {
    app: SingleInstanceAppLike;
    quit: ReturnType<typeof vi.fn>;
    listeners: Map<string, () => void>;
  } {
    const quit = vi.fn();
    const listeners = new Map<string, () => void>();
    const app: SingleInstanceAppLike = {
      requestSingleInstanceLock: () => gotLock,
      quit,
      on: (event, listener) => {
        listeners.set(event, listener);
        return app;
      },
    };
    return { app, quit, listeners };
  }

  it("quits immediately when another instance holds the lock", () => {
    const { app, quit, listeners } = makeApp(false);
    const focus = vi.fn();
    expect(installSingleInstance(app, focus)).toBe(false);
    expect(quit).toHaveBeenCalledTimes(1);
    expect(listeners.has("second-instance")).toBe(false);
  });

  it("holds the lock and focuses the window on second-instance", () => {
    const { app, quit, listeners } = makeApp(true);
    const focus = vi.fn();
    expect(installSingleInstance(app, focus)).toBe(true);
    expect(quit).not.toHaveBeenCalled();
    listeners.get("second-instance")!();
    expect(focus).toHaveBeenCalledTimes(1);
  });
});
