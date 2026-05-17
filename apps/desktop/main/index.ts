import { join } from "node:path";
import { app, BrowserWindow, ipcMain, session, webContents } from "electron";

import type { Transport } from "@enterprise-search/chat-transport";
import {
  CHANNELS,
  MockTransport,
  WebTransport,
  withBearerRefresh,
} from "@enterprise-search/chat-transport";

import {
  registerAppProtocolHandler,
  registerAppProtocolPrivilege,
} from "./app-protocol";
import { type AuthAuditLog, createFileAuthAuditLog } from "./auth";
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

  // === phase-5-followup: shared auth audit sink ===
  const auditLog = createFileAuthAuditLog({
    filePath: join(app.getPath("userData"), "audit", "auth.log"),
  });
  // === end phase-5-followup ===

  registerDeepLinks();

  const rendererDir = join(__dirname, "..", "renderer");
  registerAppProtocolHandler(rendererDir, session.defaultSession);

  // === phase-5-followup: pick transport at the seam ===
  const transport = createTransport(auditLog);
  // === end phase-5-followup ===
  const transportBridge = new TransportBridge(
    (webContentsId, payload) => {
      const target = webContents.fromId(webContentsId);
      if (target && !target.isDestroyed()) {
        target.send(CHANNELS.streamEvent, payload);
      }
    },
    { transport },
  );
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

// === phase-5-followup: transport factory ===
// Dev (no ATLAS_FACADE_URL): MockTransport — explicit, never an implicit
// default. Prod (ATLAS_FACADE_URL set): WebTransport wrapped with the
// withBearerRefresh decorator. Phase 5A's auth/oidc-client replaces the
// stub bearer + refresh hooks below; the bridge itself is sealed.
function createTransport(auditLog: AuthAuditLog): Transport {
  const facadeUrl = process.env.ATLAS_FACADE_URL;
  if (!facadeUrl) {
    return new MockTransport();
  }
  const workspaceId = process.env.ATLAS_WORKSPACE_ID ?? "wsp_unknown";
  const web = new WebTransport({
    baseUrl: facadeUrl,
    bearerProvider: () => null,
  });
  return withBearerRefresh(web, {
    workspaceId,
    refresh: async () => ({
      ok: false,
      reason: "refresh wiring deferred to Phase 5A oidc-client",
    }),
    onUnauthorizedRetry: (req) => {
      void auditLog.append({
        kind: "unauthorized-retry",
        workspaceId,
        path: req.path,
      });
    },
    onRefreshFailure: (reason) => {
      void auditLog.append({
        kind: "token-refresh-failure",
        workspaceId,
        reason,
      });
    },
  });
}
// === end phase-5-followup ===
