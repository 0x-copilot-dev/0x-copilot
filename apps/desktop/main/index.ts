import { join } from "node:path";
import { app, BrowserWindow, ipcMain, session, webContents } from "electron";

import { CHANNELS } from "@enterprise-search/chat-transport";

import {
  registerAppProtocolHandler,
  registerAppProtocolPrivilege,
} from "./app-protocol";
import { startCrashReporter } from "./crash-reporter";
import { registerDeepLinks } from "./deep-links";
import { registerIpcHandlers } from "./ipc/handlers";
import { TransportBridge } from "./transport-bridge";
import { createMainWindow } from "./window";

app.setName("Atlas");

registerAppProtocolPrivilege();

let mainWindow: BrowserWindow | null = null;
let teardownIpcHandlers: (() => void) | null = null;

void app.whenReady().then(() => {
  startCrashReporter();
  registerDeepLinks();

  const rendererDir = join(__dirname, "..", "renderer");
  registerAppProtocolHandler(rendererDir, session.defaultSession);

  const transportBridge = new TransportBridge((webContentsId, payload) => {
    const target = webContents.fromId(webContentsId);
    if (target && !target.isDestroyed()) {
      target.send(CHANNELS.streamEvent, payload);
    }
  });
  teardownIpcHandlers = registerIpcHandlers({
    ipcMain,
    bridge: transportBridge,
  });

  mainWindow = createMainWindow();
  mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) => {
    console.error("[main] renderer did-fail-load:", code, desc, url);
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow();
    }
  });
});

app.on("before-quit", () => {
  teardownIpcHandlers?.();
  teardownIpcHandlers = null;
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

// Belt + braces: deny navigation off app:// and deny window.open. The
// renderer is one URL inside one origin; anything else is a bug or attack.
app.on("web-contents-created", (_event, contents) => {
  contents.on("will-navigate", (event, url) => {
    if (!url.startsWith("app://")) {
      event.preventDefault();
    }
  });
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
});
