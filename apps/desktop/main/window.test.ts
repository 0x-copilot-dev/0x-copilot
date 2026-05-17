// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

interface CapturedConstructor {
  options: unknown;
  loadURL: ReturnType<typeof vi.fn>;
  once: ReturnType<typeof vi.fn>;
  show: ReturnType<typeof vi.fn>;
}

const { captured } = vi.hoisted(() => {
  return {
    captured: { latest: null as CapturedConstructor | null },
  };
});

vi.mock("electron", () => {
  class BrowserWindow {
    constructor(options: unknown) {
      const record: CapturedConstructor = {
        options,
        loadURL: vi.fn(),
        once: vi.fn(),
        show: vi.fn(),
      };
      captured.latest = record;
      this.loadURL = record.loadURL;
      this.once = record.once;
      this.show = record.show;
    }
    loadURL: ReturnType<typeof vi.fn>;
    once: ReturnType<typeof vi.fn>;
    show: ReturnType<typeof vi.fn>;
  }
  const protocol = {
    registerSchemesAsPrivileged: vi.fn(),
  };
  return { BrowserWindow, protocol };
});

describe("createMainWindow", () => {
  it("returns a BrowserWindow with hardened web preferences and loads the app:// origin", async () => {
    const { createMainWindow } = await import("./window");
    const win = createMainWindow({ preloadAbsPath: "/abs/preload/bridge.js" });

    expect(win).toBeDefined();
    expect(captured.latest).not.toBeNull();
    const opts = captured.latest!.options as {
      width: number;
      height: number;
      show: boolean;
      webPreferences: {
        contextIsolation: boolean;
        nodeIntegration: boolean;
        sandbox: boolean;
        webSecurity: boolean;
        preload: string;
      };
    };
    expect(opts.width).toBe(1200);
    expect(opts.height).toBe(800);
    expect(opts.show).toBe(false);
    expect(opts.webPreferences.contextIsolation).toBe(true);
    expect(opts.webPreferences.nodeIntegration).toBe(false);
    expect(opts.webPreferences.sandbox).toBe(true);
    expect(opts.webPreferences.webSecurity).toBe(true);
    expect(opts.webPreferences.preload).toBe("/abs/preload/bridge.js");

    expect(captured.latest!.loadURL).toHaveBeenCalledWith(
      "app://app/index.html",
    );
  });

  it("uses an alternate initial path when provided", async () => {
    const { createMainWindow } = await import("./window");
    createMainWindow({
      preloadAbsPath: "/abs/preload/bridge.js",
      initialPath: "/threads/123",
    });
    expect(captured.latest!.loadURL).toHaveBeenCalledWith(
      "app://app/threads/123",
    );
  });
});
