import { join } from "node:path";
import { BrowserWindow } from "electron";

import { appUrlFor } from "./app-protocol";

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
    title: "Atlas",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload,
      webSecurity: true,
      devTools: true,
    },
  });
  win.once("ready-to-show", () => {
    win.show();
  });
  void win.loadURL(appUrlFor(options.initialPath ?? "/index.html"));
  return win;
}
