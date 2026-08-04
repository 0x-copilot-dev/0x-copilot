// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

interface CapturedConstructor {
  options: unknown;
  loadURL: ReturnType<typeof vi.fn>;
  once: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
  show: ReturnType<typeof vi.fn>;
  isFullScreen: ReturnType<typeof vi.fn>;
  webContents: {
    on: ReturnType<typeof vi.fn>;
    send: ReturnType<typeof vi.fn>;
  };
}

const { captured, workArea } = vi.hoisted(() => {
  return {
    captured: { latest: null as CapturedConstructor | null },
    // Mutable so a test can shrink the "display" and assert the fit.
    workArea: { size: { width: 1920, height: 1080 } },
  };
});

vi.mock("electron", () => {
  class BrowserWindow {
    constructor(options: unknown) {
      const record: CapturedConstructor = {
        options,
        loadURL: vi.fn(),
        once: vi.fn(),
        on: vi.fn(),
        show: vi.fn(),
        isFullScreen: vi.fn(() => false),
        webContents: {
          on: vi.fn(),
          send: vi.fn(),
        },
      };
      captured.latest = record;
      this.loadURL = record.loadURL;
      this.once = record.once;
      this.on = record.on;
      this.show = record.show;
      this.isFullScreen = record.isFullScreen;
      this.webContents = record.webContents;
    }
    loadURL: ReturnType<typeof vi.fn>;
    once: ReturnType<typeof vi.fn>;
    on: ReturnType<typeof vi.fn>;
    show: ReturnType<typeof vi.fn>;
    isFullScreen: ReturnType<typeof vi.fn>;
    webContents: CapturedConstructor["webContents"];
  }
  const protocol = {
    registerSchemesAsPrivileged: vi.fn(),
  };
  const screen = {
    getPrimaryDisplay: vi.fn(() => ({ workAreaSize: workArea.size })),
  };
  return { BrowserWindow, protocol, screen };
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
      minWidth: number;
      minHeight: number;
      show: boolean;
      titleBarStyle: string;
      trafficLightPosition: { x: number; y: number };
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
    // A floor the user cannot drag past. `.desktop-window-frame` is
    // `overflow: hidden`, so a window narrower than the shell's own compact
    // breakpoint clips its chrome away instead of scrolling to it.
    expect(opts.minWidth).toBe(720);
    expect(opts.minHeight).toBe(520);
    expect(opts.show).toBe(false);
    expect(opts.titleBarStyle).toBe("hiddenInset");
    expect(opts.trafficLightPosition).toEqual({ x: 14, y: 12 });
    expect(opts.webPreferences.contextIsolation).toBe(true);
    expect(opts.webPreferences.nodeIntegration).toBe(false);
    expect(opts.webPreferences.sandbox).toBe(true);
    expect(opts.webPreferences.webSecurity).toBe(true);
    expect(opts.webPreferences.preload).toBe("/abs/preload/bridge.js");

    expect(captured.latest!.loadURL).toHaveBeenCalledWith(
      "app://app/index.html",
    );
  });

  it("fits the opening size to the display work area, never below the floor", async () => {
    const { createMainWindow } = await import("./window");

    // A work area narrower than the preferred 1200x800 — a scaled laptop panel.
    // Opening at 1200 there puts the window's right edge (where the topbar
    // keeps the ⌘K trigger) off-screen with no way to reach it.
    workArea.size = { width: 1024, height: 640 };
    createMainWindow({ preloadAbsPath: "/abs/preload/bridge.js" });
    let opts = captured.latest!.options as { width: number; height: number };
    expect(opts.width).toBe(1024);
    expect(opts.height).toBe(640);

    // Below the floor the floor wins: a window smaller than the shell's compact
    // layout is not a layout we have, so overhang beats a broken shell.
    workArea.size = { width: 500, height: 400 };
    createMainWindow({ preloadAbsPath: "/abs/preload/bridge.js" });
    opts = captured.latest!.options as { width: number; height: number };
    expect(opts.width).toBe(720);
    expect(opts.height).toBe(520);

    workArea.size = { width: 1920, height: 1080 };
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

  it("publishes initial and changing native fullscreen state", async () => {
    const { WINDOW_CHROME_STATE_CHANNEL } = await import("./window-channels");
    const { createMainWindow } = await import("./window");
    createMainWindow({ preloadAbsPath: "/abs/preload/bridge.js" });
    const record = captured.latest!;

    const didFinishLoad = record.webContents.on.mock.calls.find(
      ([event]) => event === "did-finish-load",
    )?.[1] as (() => void) | undefined;
    expect(didFinishLoad).toBeDefined();
    didFinishLoad?.();
    expect(record.webContents.send).toHaveBeenLastCalledWith(
      WINDOW_CHROME_STATE_CHANNEL,
      {
        isFullScreen: false,
        hasNativeTrafficLights: process.platform === "darwin",
      },
    );

    const enterFullScreen = record.on.mock.calls.find(
      ([event]) => event === "enter-full-screen",
    )?.[1] as (() => void) | undefined;
    const leaveFullScreen = record.on.mock.calls.find(
      ([event]) => event === "leave-full-screen",
    )?.[1] as (() => void) | undefined;
    expect(enterFullScreen).toBeDefined();
    expect(leaveFullScreen).toBeDefined();

    enterFullScreen?.();
    expect(record.webContents.send).toHaveBeenLastCalledWith(
      WINDOW_CHROME_STATE_CHANNEL,
      {
        isFullScreen: true,
        hasNativeTrafficLights: process.platform === "darwin",
      },
    );

    leaveFullScreen?.();
    expect(record.webContents.send).toHaveBeenLastCalledWith(
      WINDOW_CHROME_STATE_CHANNEL,
      {
        isFullScreen: false,
        hasNativeTrafficLights: process.platform === "darwin",
      },
    );
  });
});
