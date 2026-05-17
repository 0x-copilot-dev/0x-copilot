import {
  appendFile,
  mkdir,
  readFile,
  unlink,
  writeFile,
} from "node:fs/promises";
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

import type { AdapterGeneratedPayload } from "@enterprise-search/api-types";
import type { Transport } from "@enterprise-search/chat-transport";
import {
  CHANNELS,
  MockTransport,
  WebTransport,
  withBearerRefresh,
} from "@enterprise-search/chat-transport";

import {
  wireQualityGateForTier2,
  wireSmokeRenderExecutorForTier2,
} from "./adapters/integrate";
import {
  startTier2Lifecycle,
  type LifecycleBoundaryEvent,
  type LifecycleEventSource,
  type Tier2LifecycleHandle,
} from "./adapters/lifecycle";
import type { LifecycleEventsDeps } from "./adapters/lifecycle-events";
import {
  markBrokenFromBoundary,
  type RegistryHostDeps,
  type RendererDispatcher,
} from "./adapters/registry-host";
import {
  AuthService,
  createFileAuthAuditLog,
  type AuthAuditLog,
  type AuthMode,
} from "./auth";
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
let tier2LifecycleHandle: Tier2LifecycleHandle | null = null;

// Pluggable tier-2 event source. Phase 6 ships a no-op stub — the wiring to
// the live run-stream (subscribing to adapter_generated events) is Phase 7's
// concern. Tests inject a real source.
class StubLifecycleEventSource implements LifecycleEventSource {
  onAdapterGenerated(
    _handler: (p: AdapterGeneratedPayload) => void,
  ): () => void {
    return () => {};
  }
  onBoundaryError(
    _handler: (info: LifecycleBoundaryEvent) => void,
  ): () => void {
    return () => {};
  }
}

class WindowDispatcher implements RendererDispatcher {
  send(channel: string, payload: unknown): void {
    if (mainWindow === null) return;
    if (mainWindow.isDestroyed()) return;
    mainWindow.webContents.send(channel, payload);
  }
}

void app.whenReady().then(() => {
  startCrashReporter();
  registerDeepLinks();
  wireQualityGateForTier2();
  wireSmokeRenderExecutorForTier2();

  const rendererDir = join(__dirname, "..", "renderer");
  registerAppProtocolHandler(rendererDir, session.defaultSession);

  const auditLog = createFileAuthAuditLog({
    filePath: join(app.getPath("userData"), "audit", "auth.log"),
  });
  const authService = buildAuthService();
  const transport = createTransport(authService, auditLog);

  const transportBridge = new TransportBridge(
    (webContentsId, payload) => {
      const target = webContents.fromId(webContentsId);
      if (target && !target.isDestroyed()) {
        target.send(CHANNELS.streamEvent, payload);
      }
    },
    { transport },
  );

  const tier2Source = new StubLifecycleEventSource();
  const userDataDir = app.getPath("userData");
  const adapterDir = join(userDataDir, "adapters");
  const audit: LifecycleEventsDeps = {
    logPath: join(userDataDir, "audit", "adapter-lifecycle.log"),
    fs: {
      appendFile,
      mkdir,
      readFile: async (path, _encoding) => readFile(path, "utf8"),
    },
  };
  const dispatcher = new WindowDispatcher();
  const hostDeps: RegistryHostDeps = {
    adapterDir,
    clock: Date.now,
    dispatcher,
    audit,
    installer: { fs: { writeFile, mkdir, unlink } },
  };

  teardownIpcHandlers = registerIpcHandlers({
    ipcMain,
    bridge: transportBridge,
    auth: {
      signIn: (workspaceId) => authService.signIn(workspaceId),
      signOut: (workspaceId) => authService.signOut(workspaceId),
      getSession: (workspaceId) => authService.getSession(workspaceId),
      refresh: (workspaceId) => authService.refresh(workspaceId),
    },
    tier2: {
      onBoundaryError: (payload) => {
        void markBrokenFromBoundary(
          {
            scheme: payload.scheme,
            version: payload.version,
            method: payload.method,
            reason: payload.message,
          },
          hostDeps,
        );
      },
    },
  });

  tier2LifecycleHandle = startTier2Lifecycle({
    source: tier2Source,
    host: hostDeps,
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
  tier2LifecycleHandle?.stop();
  tier2LifecycleHandle = null;
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
  getBearerCachedSync(workspaceId: string): string | null;
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
    getBearerCachedSync: (workspaceId) =>
      service.getBearerCachedSync(workspaceId),
    activeWorkspace: () => service.activeWorkspace(),
  };
}

// Dev (no ATLAS_FACADE_URL): MockTransport — explicit, never an implicit
// default. Prod: WebTransport with the AuthService-backed bearer provider,
// wrapped with withBearerRefresh to retry once on 401 by calling
// authService.refresh. Auth audit events fire for the retry path.
function createTransport(
  authService: ActiveAuthService,
  auditLog: AuthAuditLog,
): Transport {
  const facadeUrl = process.env.ATLAS_FACADE_URL;
  if (!facadeUrl) {
    return new MockTransport();
  }
  const web = new WebTransport({
    baseUrl: facadeUrl,
    bearerProvider: () => {
      const ws = authService.activeWorkspace();
      if (ws === null) return null;
      return authService.getBearerCachedSync(ws);
    },
  });
  return withBearerRefresh(web, {
    workspaceId: process.env.ATLAS_WORKSPACE_ID ?? "wsp_unknown",
    refresh: async (workspaceId) => {
      const ws = authService.activeWorkspace() ?? workspaceId;
      try {
        const next = await authService.refresh(ws);
        if (next === null)
          return { ok: false, reason: "no session to refresh" };
        return { ok: true };
      } catch (err) {
        return {
          ok: false,
          reason: err instanceof Error ? err.message : String(err),
        };
      }
    },
    onUnauthorizedRetry: (req) => {
      const ws = authService.activeWorkspace() ?? "wsp_unknown";
      void auditLog.append({
        kind: "unauthorized-retry",
        workspaceId: ws,
        path: req.path,
      });
    },
    onRefreshFailure: (reason) => {
      const ws = authService.activeWorkspace() ?? "wsp_unknown";
      void auditLog.append({
        kind: "token-refresh-failure",
        workspaceId: ws,
        reason,
      });
    },
  });
}
