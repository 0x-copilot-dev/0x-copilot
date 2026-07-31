import { join } from "node:path";
import { BrowserWindow } from "electron";

import { appUrlFor } from "./app-protocol";
import {
  WINDOW_CHROME_STATE_CHANNEL,
  type WindowChromeState,
} from "./window-channels";

export interface CreateMainWindowOptions {
  readonly preloadAbsPath?: string;
  readonly initialPath?: string;
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
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
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
