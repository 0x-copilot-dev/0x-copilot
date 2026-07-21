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

  it("supervises in dev when COPILOT_RUNTIME_DIR points at a staged runtime", () => {
    expect(
      shouldSupervise({
        isPackaged: false,
        env: { COPILOT_RUNTIME_DIR: "/repo/apps/desktop/resources" },
      }),
    ).toBe(true);
  });

  it("supervises for the shipped CLI launch (isPackaged=false, COPILOT_RUNTIME_DIR + COPILOT_PRODUCTION=1)", () => {
    // tools/cli/lib/launch.mjs sets both; the app must boot the embedded stack.
    expect(
      shouldSupervise({
        isPackaged: false,
        env: {
          COPILOT_RUNTIME_DIR: "/home/u/.0xcopilot",
          COPILOT_PRODUCTION: "1",
        },
      }),
    ).toBe(true);
  });

  it("does NOT supervise on COPILOT_PRODUCTION=1 alone (auth signal, not a supervise signal)", () => {
    // COPILOT_PRODUCTION without a staged runtime dir means "production auth
    // against an external facade" — no local stack to supervise. posture.ts
    // still resolves production posture from COPILOT_PRODUCTION directly.
    expect(
      shouldSupervise({
        isPackaged: false,
        env: { COPILOT_PRODUCTION: "1" },
      }),
    ).toBe(false);
  });

  it("does NOT supervise plain dev (COPILOT_FACADE_URL flow unchanged)", () => {
    expect(
      shouldSupervise({
        isPackaged: false,
        env: { COPILOT_FACADE_URL: "http://127.0.0.1:8200" },
      }),
    ).toBe(false);
    expect(
      shouldSupervise({ isPackaged: false, env: { COPILOT_RUNTIME_DIR: "" } }),
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
