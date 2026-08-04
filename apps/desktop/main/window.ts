import { join } from "node:path";
import { BrowserWindow, screen } from "electron";

import { appUrlFor } from "./app-protocol";
import {
  WINDOW_CHROME_STATE_CHANNEL,
  type WindowChromeState,
} from "./window-channels";

export interface CreateMainWindowOptions {
  readonly preloadAbsPath?: string;
  readonly initialPath?: string;
}

/** The size the window opens at when the display has room for it. */
const PREFERRED_WIDTH = 1200;
const PREFERRED_HEIGHT = 800;

/**
 * The narrowest the shell is designed to render.
 *
 * 720 mirrors `SHELL_BREAKPOINTS.compact` (packages/chat-surface/src/shell/
 * layout.ts) — the width below which the surface drops to one column. It is
 * restated rather than imported because this is Electron MAIN-process code and
 * the constant lives behind a React package's barrel; the same restate-with-a-
 * pointer pattern already governs `--desktop-app-rail-width` in desktop.css.
 *
 * Without a floor the user can drag the window narrower than any layout the
 * shell has, and because `.desktop-window-frame` is `overflow: hidden` the
 * surplus is clipped instead of scrollable — the chrome does not get cramped,
 * it vanishes.
 */
const MIN_WIDTH = 720;
const MIN_HEIGHT = 520;

/**
 * Fit the preferred size to the display actually in front of the user.
 *
 * A hard-coded 1200x800 is only correct on a display with 1200x800 of work
 * area. On anything smaller — a scaled laptop panel, a half-height work area
 * under a large Dock — the window opens larger than the screen, and macOS
 * leaves the overhanging edge (which is where the topbar keeps the ⌘K trigger)
 * off-screen with no way to reach it short of manually resizing.
 *
 * `screen` is only usable once the app is ready. `createMainWindow` is always
 * called after that (constructing a `BrowserWindow` requires it too), but the
 * lookup is still guarded so a missing/mocked module degrades to the preferred
 * size rather than throwing during window creation.
 */
function initialWindowSize(): { width: number; height: number } {
  const workArea = screen?.getPrimaryDisplay?.()?.workAreaSize;
  if (workArea === undefined) {
    return { width: PREFERRED_WIDTH, height: PREFERRED_HEIGHT };
  }
  return {
    // The floor wins over the display fit: a window smaller than MIN_* is not a
    // layout we have, so on a very small work area we would rather overhang
    // than paint a broken shell.
    width: Math.max(MIN_WIDTH, Math.min(PREFERRED_WIDTH, workArea.width)),
    height: Math.max(MIN_HEIGHT, Math.min(PREFERRED_HEIGHT, workArea.height)),
  };
}

// One BrowserWindow per app session (PRD D9). Security flags are
// non-negotiable per the architecture spec:
//   contextIsolation: true   — renderer ↔ main types isolated
//   nodeIntegration: false   — no node globals in renderer
//   sandbox: true            — Chromium renderer sandbox
//   webSecurity: true        — enforce same-origin / CSP
// devTools left on at compile time; Phase 8 picks the production policy.
export function createMainWindow(
  options: CreateMainWindowOptions = {},
): BrowserWindow {
  const preload =
    options.preloadAbsPath ?? join(__dirname, "..", "preload", "bridge.js");
  const { width, height } = initialWindowSize();
  const win = new BrowserWindow({
    width,
    height,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    show: false,
    backgroundColor: "#101113",
    title: "0xCopilot",
    titleBarStyle: "hiddenInset",
    // Align the native controls with the compact 38px application header.
    // macOS is the only platform that consumes this option.
    trafficLightPosition: { x: 14, y: 12 },
    // Windows/Linux take the taskbar + window icon from the window; macOS
    // ignores this and uses the bundle/dock icon (main/branding.ts).
    ...(process.platform === "darwin"
      ? {}
      : { icon: join(__dirname, "icon.png") }),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload,
      webSecurity: true,
      devTools: true,
    },
  });
  const sendWindowChromeState = (isFullScreen: boolean): void => {
    const state: WindowChromeState = {
      isFullScreen,
      hasNativeTrafficLights: process.platform === "darwin",
    };
    win.webContents.send(WINDOW_CHROME_STATE_CHANNEL, state);
  };
  // Native fullscreen removes the macOS traffic lights. Tell the renderer so
  // it can return the turbine to the rail instead of leaving horizontal
  // clearance for controls that are no longer present.
  win.on("enter-full-screen", () => {
    sendWindowChromeState(true);
  });
  win.on("leave-full-screen", () => {
    sendWindowChromeState(false);
  });
  // Preload buffers this snapshot until React subscribes, so the initial paint
  // and reload path cannot miss the current native-window state.
  win.webContents.on("did-finish-load", () => {
    sendWindowChromeState(win.isFullScreen());
  });
  win.once("ready-to-show", () => {
    win.show();
  });
  void win.loadURL(appUrlFor(options.initialPath ?? "/index.html"));
  return win;
}
