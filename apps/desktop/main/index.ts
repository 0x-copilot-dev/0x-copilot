import { join } from "node:path";
import {
  app,
  BrowserWindow,
  ipcMain,
  safeStorage,
  session,
  shell,
  webContents,
} from "electron";

import { CHANNELS } from "@enterprise-search/chat-transport";

import {
  registerAppProtocolHandler,
  registerAppProtocolPrivilege,
} from "./app-protocol";
import { AuthService, type AuthMode } from "./auth";
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

  const authService = buildAuthService();
  const transportBridge = new TransportBridge(
    (webContentsId, payload) => {
      const target = webContents.fromId(webContentsId);
      if (target && !target.isDestroyed()) {
        target.send(CHANNELS.streamEvent, payload);
      }
    },
    {
      bearerProvider: async () => {
        const ws = authService.activeWorkspace();
        if (ws === null) return null;
        return authService.getBearer(ws);
      },
    },
  );
  teardownIpcHandlers = registerIpcHandlers({
    ipcMain,
    bridge: transportBridge,
    auth: {
      signIn: (workspaceId) => authService.signIn(workspaceId),
      signOut: (workspaceId) => authService.signOut(workspaceId),
      getSession: (workspaceId) => authService.getSession(workspaceId),
      refresh: (workspaceId) => authService.refresh(workspaceId),
    },
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

app.on("web-contents-created", (_event, contents) => {
  contents.on("will-navigate", (event, url) => {
    if (!url.startsWith("app://")) {
      event.preventDefault();
    }
  });
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
});

interface ActiveAuthService {
  signIn(workspaceId: string): ReturnType<AuthService["signIn"]>;
  signOut(workspaceId: string): ReturnType<AuthService["signOut"]>;
  getSession(workspaceId: string): ReturnType<AuthService["getSession"]>;
  refresh(workspaceId: string): ReturnType<AuthService["refresh"]>;
  getBearer(workspaceId: string): Promise<string | null>;
  activeWorkspace(): string | null;
}

function buildAuthService(): ActiveAuthService {
  const mode: AuthMode =
    process.env.ATLAS_AUTH_MODE === "oidc" ? "oidc" : "dev-mint";
  const facadeBaseUrl = process.env.ATLAS_FACADE_URL ?? "http://127.0.0.1:8200";
  const devPersonaSlug = process.env.ATLAS_DEV_PERSONA ?? "sarah_acme";
  const allowPlaintext =
    process.env.BACKEND_ENVIRONMENT === "development" ||
    process.env.ATLAS_AUTH_MODE === "dev-mint";

  let oidcConfig: ConstructorParameters<typeof AuthService>[0]["oidc"];
  if (mode === "oidc") {
    const issuer = process.env.ATLAS_OIDC_ISSUER ?? "";
    const clientId = process.env.ATLAS_OIDC_CLIENT_ID ?? "";
    const authEp =
      process.env.ATLAS_OIDC_AUTHORIZATION_ENDPOINT ?? `${issuer}/authorize`;
    const tokenEp = process.env.ATLAS_OIDC_TOKEN_ENDPOINT ?? `${issuer}/token`;
    const scopes = (
      process.env.ATLAS_OIDC_SCOPES ?? "openid profile email"
    ).split(/\s+/u);
    if (issuer === "" || clientId === "") {
      throw new Error(
        "ATLAS_AUTH_MODE=oidc requires ATLAS_OIDC_ISSUER and ATLAS_OIDC_CLIENT_ID",
      );
    }
    oidcConfig = {
      issuer,
      clientId,
      authorizationEndpoint: authEp,
      tokenEndpoint: tokenEp,
      scopes,
    };
  }

  const service = new AuthService({
    mode,
    facadeBaseUrl,
    devPersonaSlug,
    oidc: oidcConfig,
    userDataDir: app.getPath("userData"),
    safeStorage,
    openExternal: (url) => shell.openExternal(url),
    allowPlaintextFallback: allowPlaintext,
  });

  return {
    signIn: (workspaceId) => service.signIn(workspaceId),
    signOut: (workspaceId) => service.signOut(workspaceId),
    getSession: (workspaceId) => service.getSession(workspaceId),
    refresh: (workspaceId) => service.refresh(workspaceId),
    getBearer: (workspaceId) => service.getBearer(workspaceId),
    activeWorkspace: () => service.activeWorkspace(),
  };
}
