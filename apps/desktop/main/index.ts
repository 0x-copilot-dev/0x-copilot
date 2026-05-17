import { join } from "node:path";
import { app, BrowserWindow, session } from "electron";

import {
  registerAppProtocolHandler,
  registerAppProtocolPrivilege,
} from "./app-protocol";
import { startCrashReporter } from "./crash-reporter";
import { registerDeepLinks } from "./deep-links";
import { createMainWindow } from "./window";

app.setName("Atlas");

// Must run before app.ready — registers app:// as standard / secure /
// fetch-capable so CSP and same-origin policy apply normally (PRD D24
// pattern; S2 friction note 2).
registerAppProtocolPrivilege();

let mainWindow: BrowserWindow | null = null;

void app.whenReady().then(() => {
  startCrashReporter();
  registerDeepLinks();

  const rendererDir = join(__dirname, "..", "renderer");
  registerAppProtocolHandler(rendererDir, session.defaultSession);

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
