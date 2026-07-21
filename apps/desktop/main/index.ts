import { existsSync } from "node:fs";
import {
  appendFile,
  chmod,
  mkdir,
  readFile,
  unlink,
  writeFile,
} from "node:fs/promises";
import { join } from "node:path";
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  safeStorage,
  session,
  shell,
  webContents,
} from "electron";
// Named import: electron-updater is CJS with no default export, so a default
// import bundles to `undefined` under esbuild's interop.
import { autoUpdater as electronAutoUpdater } from "electron-updater";

import type {
  BootStatusPayload,
  Transport,
  UpdateStatusPayload,
} from "@0x-copilot/chat-transport";
import {
  CHANNELS,
  MockTransport,
  WebTransport,
  withBearerRefresh,
} from "@0x-copilot/chat-transport";

import { initAutoUpdate, type AutoUpdateHandle } from "./updater";

import {
  wireQualityGateForTier2,
  wireSmokeRenderExecutorForTier2,
} from "./adapters/integrate";
import {
  startTier2Lifecycle,
  type Tier2LifecycleHandle,
} from "./adapters/lifecycle";
import {
  RunFeedLifecycleEventSource,
  type LifecycleEventsDeps,
} from "./adapters/lifecycle-events";
import type {
  RegistryHostDeps,
  RendererDispatcher,
} from "./adapters/registry-host";
import {
  createFileConsentAckStore,
  createInstallReviewGate,
  type InstallConsentRequest,
  type InstallReviewGate,
} from "./adapters/review-gate";
import { AuthService, createFileAuthAuditLog, type AuthAuditLog } from "./auth";
import {
  registerAppProtocolHandler,
  registerAppProtocolPrivilege,
} from "./app-protocol";
import {
  createCapabilityService,
  isDesktopFilesystemEnabled,
  type CapabilityService,
} from "./capabilities";
import { ConnectorService } from "./connectors/connector-service";
import { startCrashReporter } from "./crash-reporter";
import { registerDeepLinks } from "./deep-links";
import { registerIpcHandlers } from "./ipc/handlers";
import { applyBrandDockIcon, applyBrandIdentity } from "./branding";
import { resolveAuthPosture } from "./posture";
import { installSingleInstance, shouldSupervise } from "./services/boot-mode";
import {
  setBootSecretsEncryption,
  type BootSecretsFs,
} from "./services/boot-secrets";
import { createDesktopSupervisor } from "./services/desktop-supervisor";
import { applyBundledGoogleOAuth } from "./services/google-oauth-default";
import { SECURE_STORAGE_CHANNELS } from "./services/secure-storage-channels";
import { FIRST_RUN_CHANNELS } from "./services/first-run-channels";
import {
  loadFirstRunComplete,
  saveFirstRunComplete,
} from "./services/first-run-store";
import {
  gatedSafeStorage,
  loadSecureStorageMode,
  saveSecureStorageMode,
  type SecureStorageMode,
} from "./services/secure-storage-policy";
import type { ServiceSupervisor } from "./services/supervisor";
import { TransportBridge } from "./transport-bridge";
import { createMainWindow } from "./window";

applyBrandIdentity(app, { platform: process.platform });

// Test-harness isolation: an explicit userData SUBDIR keeps a driven run
// (tools/cli-testing) fully hermetic — its own boot secrets, embedded-PG
// data dir and sessions — so it never touches (or wipes) a real install's
// data. Must run before anything reads app.getPath("userData"). The
// cli-testing driver has set this env for dev posture since it shipped;
// honoring it here (all postures) makes that contract real.
{
  const subdir = process.env.COPILOT_DESKTOP_USER_DATA_SUBDIR ?? "";
  if (subdir !== "" && !subdir.includes("..") && !subdir.includes("/")) {
    app.setPath("userData", join(app.getPath("userData"), subdir));
  }
}

registerAppProtocolPrivilege();

let mainWindow: BrowserWindow | null = null;
let teardownIpcHandlers: (() => void) | null = null;
// Secure-storage policy (Settings → Key storage & app lock). Read once at
// boot; flipped in place by the IPC toggle so future store writes follow the
// new mode without a restart. `storesSafeStorage` is what the auth + grant
// stores receive: in "file" mode it reports encryption unavailable (their
// chmod-600 plaintext paths activate — no keychain prompt), while decrypt
// still delegates to the real safeStorage so legacy cipher blobs stay
// readable.
let secureStorageMode: SecureStorageMode = "file";
const storesSafeStorage = gatedSafeStorage(
  safeStorage,
  () => secureStorageMode,
);
const bootSecretsFs: BootSecretsFs = { readFile, writeFile, mkdir, chmod };
let tier2LifecycleHandle: Tier2LifecycleHandle | null = null;
let supervisor: ServiceSupervisor | null = null;
let supervisorStopped = false;
let capabilityService: CapabilityService | null = null;
// AC9 — desktop connector OAuth service. Constructed once the facade is
// reachable (WebTransport mode). Held at module scope so the deep-link
// dispatcher (registered eagerly at boot) can route connector OAuth callbacks
// to it by state without re-registering the protocol handler.
let connectorService: ConnectorService | null = null;
let latestBootStatus: BootStatusPayload | null = null;
let updateHandle: AutoUpdateHandle | null = null;
let latestUpdateStatus: UpdateStatusPayload | null = null;

// One app instance at a time: the packaged build owns an embedded
// postgres data dir — two postmasters on one cluster corrupt it.
const hasSingleInstanceLock = installSingleInstance(app, () => {
  if (mainWindow === null || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
});

// PRD-10 review gate wiring. Read-only generated adapters auto-install; a
// write/diff-surface adapter requires a one-time consent acknowledgment,
// surfaced through the desktop's native message-box (the same native-consent
// posture the folder-grant picker uses). The acknowledgment persists per scheme
// under userData so the prompt is genuinely one-time.
function buildTier2ReviewGate(userDataDir: string): InstallReviewGate {
  const store = createFileConsentAckStore({
    filePath: join(userDataDir, "adapters", "consent-acknowledged.json"),
    fs: {
      readFile: (path) => readFile(path, "utf8"),
      writeFile,
      mkdir,
    },
  });
  const prompt = async (request: InstallConsentRequest): Promise<boolean> => {
    const parent =
      mainWindow !== null && !mainWindow.isDestroyed() ? mainWindow : undefined;
    const options: Electron.MessageBoxOptions = {
      type: "warning",
      buttons: ["Cancel", "Allow"],
      defaultId: 1,
      cancelId: 0,
      title: "Install a generated view?",
      message: `Allow a generated view for "${request.scheme}" that can render editable changes?`,
      detail:
        "This adapter was produced by the agent and renders a write/diff " +
        "surface. Approving any change it shows still requires a separate " +
        `confirmation. Generator: ${request.generatorModel}.`,
      noLink: true,
    };
    const result = parent
      ? await dialog.showMessageBox(parent, options)
      : await dialog.showMessageBox(options);
    return result.response === 1;
  };
  return createInstallReviewGate({ store, prompt });
}

class WindowDispatcher implements RendererDispatcher {
  send(channel: string, payload: unknown): void {
    if (mainWindow === null) return;
    if (mainWindow.isDestroyed()) return;
    mainWindow.webContents.send(channel, payload);
  }
}

function sendBootStatus(status: BootStatusPayload): void {
  latestBootStatus = status;
  if (mainWindow === null || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send(CHANNELS.bootStatus, status);
}

function sendUpdateStatus(status: UpdateStatusPayload): void {
  latestUpdateStatus = status;
  if (mainWindow === null || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send(CHANNELS.updateStatus, status);
}

// electron-updater auto-update, active only in a packaged, signed build that
// carries update metadata (app-update.yml). Unsigned/dev builds no-op. Runs
// independently of the service supervisor: an update downloads in the
// background and installs on the NEXT quit, never mid-run.
function startAutoUpdate(): void {
  try {
    const hasUpdateConfig =
      app.isPackaged &&
      existsSync(join(process.resourcesPath, "app-update.yml"));
    updateHandle = initAutoUpdate({
      // electron-updater's autoUpdater matches AutoUpdaterLike structurally.
      autoUpdater: electronAutoUpdater,
      isPackaged: app.isPackaged,
      hasUpdateConfig,
      emit: sendUpdateStatus,
      log: (message) => {
        console.log("[updater]", message);
      },
    });
  } catch (err) {
    // Auto-update is never allowed to block or crash boot.
    console.error("[updater] init failed (continuing without updates):", err);
  }
}

// Builds the capability service (grant store + native picker + loopback
// broker) and starts the broker. The broker token is minted per boot and is
// delivered OUT OF BAND to intended children (slice 2 wiring); it is never
// logged and never crosses renderer IPC. A broker failure must never block
// boot — the app is fully usable without host-folder grants.
function startCapabilitySubsystem(): void {
  try {
    // Plaintext is legitimate in dev AND whenever the user's secure-storage
    // policy is "file" (the default) — the gated safeStorage reports
    // encryption unavailable then, and the store must not fail closed on the
    // user's own choice.
    const allowPlaintext =
      process.env.BACKEND_ENVIRONMENT === "development" ||
      process.env.COPILOT_AUTH_MODE === "dev-mint" ||
      secureStorageMode === "file";
    capabilityService = createCapabilityService({
      userDataDir: app.getPath("userData"),
      safeStorage: storesSafeStorage,
      showOpenDialog: async () => {
        // Main owns the path: the renderer never supplies or receives one.
        const parent =
          mainWindow !== null && !mainWindow.isDestroyed()
            ? mainWindow
            : undefined;
        const result =
          parent !== undefined
            ? await dialog.showOpenDialog(parent, {
                properties: ["openDirectory"],
              })
            : await dialog.showOpenDialog({ properties: ["openDirectory"] });
        return { canceled: result.canceled, filePaths: result.filePaths };
      },
      allowPlaintextFallback: allowPlaintext,
    });
    capabilityService
      .startBroker()
      .then((handle) => {
        // baseUrl (host+port) is non-secret; the token is NOT logged.
        console.log("[capability-broker] listening on", handle.baseUrl);
      })
      .catch((err: unknown) => {
        console.error("[capability-broker] failed to start:", err);
      });
  } catch (err) {
    console.error("[capabilities] init failed (continuing without):", err);
  }
}

// Registers the Settings toggle's IPC surface. `set` performs the boot-secrets
// migration with the REAL safeStorage (enabling is the one user-initiated
// moment the macOS keychain prompt belongs to), persists the policy, and flips
// the live mode.
function registerSecureStorageIpc(): void {
  ipcMain.handle(SECURE_STORAGE_CHANNELS.get, () => ({
    mode: secureStorageMode,
    keychainAvailable: safeStorage.isEncryptionAvailable(),
  }));
  ipcMain.handle(SECURE_STORAGE_CHANNELS.set, async (_event, payload) => {
    const enabled =
      typeof payload === "object" &&
      payload !== null &&
      (payload as Record<string, unknown>).enabled === true;
    try {
      await setBootSecretsEncryption({
        userDataDir: app.getPath("userData"),
        safeStorage,
        fs: bootSecretsFs,
        enabled,
      });
      secureStorageMode = enabled ? "keychain" : "file";
      saveSecureStorageMode(app.getPath("userData"), secureStorageMode);
      return { ok: true, mode: secureStorageMode };
    } catch (err) {
      console.error("[secure-storage] toggle failed:", err);
      return {
        ok: false,
        mode: secureStorageMode,
        error: err instanceof Error ? err.message : "unknown error",
      };
    }
  });
}

// Extract a non-empty workspaceId from a renderer IPC payload. Caller-supplied
// identity is untrusted; we only accept it as a namespacing key for this
// per-install UX flag (never as an auth claim).
function readWorkspaceId(payload: unknown): string | null {
  if (typeof payload === "object" && payload !== null) {
    const wid = (payload as Record<string, unknown>).workspaceId;
    if (typeof wid === "string" && wid.length > 0) return wid;
  }
  return null;
}

// Registers the first-run (FTUE) completion IPC. The renderer's FirstRunGate
// reads `get` to decide whether to show onboarding, and writes `set` when the
// user finishes/skips onboarding. Per-workspace, persisted chmod-600. A read
// error yields `completed: false` so onboarding fails OPEN (never trap a user
// past onboarding on a bad read).
function registerFirstRunIpc(): void {
  ipcMain.handle(FIRST_RUN_CHANNELS.get, (_event, payload) => {
    const workspaceId = readWorkspaceId(payload);
    if (workspaceId === null) return { completed: false };
    try {
      return {
        completed: loadFirstRunComplete(app.getPath("userData"), workspaceId),
      };
    } catch (err) {
      console.error("[first-run] read failed:", err);
      return { completed: false };
    }
  });
  ipcMain.handle(FIRST_RUN_CHANNELS.set, (_event, payload) => {
    const workspaceId = readWorkspaceId(payload);
    if (workspaceId === null) {
      return { ok: false, error: "missing workspaceId" };
    }
    // Default true: `set` is called to MARK onboarding done; an explicit
    // `completed: false` resets it (used only by tests/dev).
    const completed = !(
      typeof payload === "object" &&
      payload !== null &&
      (payload as Record<string, unknown>).completed === false
    );
    try {
      saveFirstRunComplete(app.getPath("userData"), workspaceId, completed);
      return { ok: true, completed };
    } catch (err) {
      console.error("[first-run] persist failed:", err);
      return {
        ok: false,
        error: err instanceof Error ? err.message : "unknown error",
      };
    }
  });
}

if (hasSingleInstanceLock) {
  void app.whenReady().then(() => {
    startCrashReporter();
    secureStorageMode = loadSecureStorageMode(app.getPath("userData"));
    registerSecureStorageIpc();
    registerFirstRunIpc();
    applyBrandDockIcon(app, {
      platform: process.platform,
      iconPngPath: join(__dirname, "icon.png"),
    });
    // AC9: route connector OAuth deep-link callbacks (keyed on the unique
    // 256-bit state) to the connector coordinator BEFORE app-login. A state
    // the connector service does not own returns false and falls through.
    registerDeepLinks({
      connectorCallbackRouter: (code, state) =>
        connectorService?.handleDeepLinkCallback(code, state) ?? false,
    });
    wireQualityGateForTier2();
    wireSmokeRenderExecutorForTier2();

    const rendererDir = join(__dirname, "..", "renderer");
    registerAppProtocolHandler(rendererDir, session.defaultSession);

    // Boot screen immediately: the window exists (renderer shows
    // BootProgress) before any service work starts. If the renderer
    // finishes loading after a status was already pushed, replay the
    // latest one so it never misses the current phase.
    mainWindow = createMainWindow();
    mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) => {
      console.error("[main] renderer did-fail-load:", code, desc, url);
    });
    mainWindow.webContents.on("did-finish-load", () => {
      if (latestBootStatus !== null) sendBootStatus(latestBootStatus);
      if (latestUpdateStatus !== null) sendUpdateStatus(latestUpdateStatus);
    });

    // Background auto-update (packaged+signed only; no-op otherwise). Kept
    // independent of the boot path so a boot failure never blocks updates and
    // an update never interrupts a run.
    startAutoUpdate();

    // Capability subsystem (AC5): folder-grant model + loopback broker. Built
    // here so the picker can parent its dialog to the main window; started
    // defensively so a broker bind failure never blocks boot. G4: gated behind
    // RUNTIME_ENABLE_DESKTOP_FILESYSTEM, read ONCE at boot — when unset/false
    // the broker never binds and (because capabilityService stays null) the
    // capability IPC channels are never registered, so calls fail closed.
    if (isDesktopFilesystemEnabled(process.env)) {
      startCapabilitySubsystem();
    } else {
      console.log(
        "[capabilities] desktop filesystem disabled " +
          "(set RUNTIME_ENABLE_DESKTOP_FILESYSTEM=1 to enable)",
      );
    }

    if (shouldSupervise({ isPackaged: app.isPackaged, env: process.env })) {
      // Seed the bundled-default Google OAuth client (id + secret) into the env
      // BEFORE the supervisor builds child envs, so "Continue with Google" works
      // out of the box. An operator GOOGLE_OAUTH_CLIENT_ID env var still wins;
      // the credentials live in a gitignored google-oauth.json next to the app
      // (shipped in the npm payload, never in git — the repo is public).
      const googleOAuth = applyBundledGoogleOAuth(
        process.env,
        app.getAppPath(),
      );
      console.log(`[auth] google oauth client source: ${googleOAuth.applied}`);
      supervisor = createDesktopSupervisor({
        userDataDir: app.getPath("userData"),
        safeStorage,
        secureStorageMode,
        resourcesPath: process.resourcesPath,
        runtimeDirOverride: process.env.COPILOT_RUNTIME_DIR,
      });
      supervisor.onStatus(sendBootStatus);
      supervisor
        .start()
        .then(({ facadeUrl, hostToken }) => {
          wireTransportAndIpc(facadeUrl, hostToken);
        })
        .catch((err: unknown) => {
          // The supervisor already emitted a fatal BootStatus for the
          // renderer's fatal screen; keep the process alive so the user
          // can read it.
          console.error("[main] supervised boot failed:", err);
        });
    } else {
      // Dev mode (`npm run dev`, no COPILOT_RUNTIME_DIR): no supervisor.
      // COPILOT_FACADE_URL selects WebTransport; otherwise MockTransport.
      wireTransportAndIpc(process.env.COPILOT_FACADE_URL);
      sendBootStatus({ phase: "ready", message: "Ready", percent: 100 });
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = createMainWindow();
      }
    });
  });
}

// Constructed only once the facade is reachable (supervised mode) or
// immediately in dev mode. facadeUrl === undefined -> MockTransport.
function wireTransportAndIpc(
  facadeUrl: string | undefined,
  hostToken?: string,
): void {
  const auditLog = createFileAuthAuditLog({
    filePath: join(app.getPath("userData"), "audit", "auth.log"),
  });
  const authService = buildAuthService(auditLog, facadeUrl, hostToken);
  const transport = createTransport(authService, auditLog, facadeUrl);

  // AC9 — connector OAuth service. Only meaningful against a real facade
  // (MockTransport dev has no connector backend), so it is null in mock mode
  // and the connector IPC channels are simply never registered (fail closed).
  connectorService =
    facadeUrl === undefined
      ? null
      : new ConnectorService({
          facadeBaseUrl: facadeUrl,
          openExternal: (url) => shell.openExternal(url),
          getBearer: async () => {
            const ws = authService.activeWorkspace();
            return ws === null ? null : authService.getBearer(ws);
          },
        });

  // PRD-10 — the real tier-2 lifecycle source. It observes `adapter_generated`
  // events off the same run-feed SSE stream the UI consumes (the TransportBridge
  // tap below) and live render failures off the renderer's boundary-error IPC.
  const tier2Source = new RunFeedLifecycleEventSource();

  const transportBridge = new TransportBridge(
    (webContentsId, payload) => {
      const target = webContents.fromId(webContentsId);
      if (target && !target.isDestroyed()) {
        target.send(CHANNELS.streamEvent, payload);
      }
    },
    {
      transport,
      onRunFeedMessage: (raw) => tier2Source.feedStreamMessage(raw),
    },
  );

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
    reviewGate: buildTier2ReviewGate(userDataDir),
  };

  teardownIpcHandlers = registerIpcHandlers({
    ipcMain,
    bridge: transportBridge,
    auth: {
      signIn: (workspaceId) => authService.signIn(workspaceId),
      signInWithGoogle: (workspaceId) =>
        authService.signInWithGoogle(workspaceId),
      signInWithWallet: (workspaceId) =>
        authService.signInWithWallet(workspaceId),
      linkGoogle: (workspaceId) => authService.linkGoogle(workspaceId),
      linkWallet: (workspaceId, confirmMerge) =>
        authService.linkWallet(workspaceId, confirmMerge),
      signOut: (workspaceId) => authService.signOut(workspaceId),
      getSession: (workspaceId) => authService.getSession(workspaceId),
      refresh: (workspaceId) => authService.refresh(workspaceId),
      getPosture: () => ({
        productionPosture: authService.isProductionPosture(),
      }),
    },
    tier2: {
      onBoundaryError: (payload) => {
        // Route through the lifecycle source so the boundary drives the demote
        // path AND the per-scheme retry counter (handleBoundaryError), rather
        // than calling markBrokenFromBoundary directly and skipping the counter.
        tier2Source.feedBoundaryError({
          scheme: payload.scheme,
          version: payload.version,
          method: payload.method,
          reason: payload.message,
        });
      },
    },
    // AC5 capability channels. Only wired when the subsystem constructed;
    // returns only the renderer-safe grant view (no host path / broker token).
    capability:
      capabilityService === null
        ? undefined
        : {
            requestFolderGrant: (params) =>
              capabilityService!.requestFolderGrant(params),
            listGrants: () => capabilityService!.listGrants(),
            revokeGrant: (grantId) => capabilityService!.revokeGrant(grantId),
          },
    // AC9 connector channels. Wired only against a real facade; returns only
    // the renderer-safe catalog + connection metadata (no provider token).
    connectors:
      connectorService === null
        ? undefined
        : {
            listCatalog: () => connectorService!.listCatalog(),
            connect: (slug, options) =>
              connectorService!.connect(slug, options),
          },
  });

  tier2LifecycleHandle = startTier2Lifecycle({
    source: tier2Source,
    host: hostDeps,
  });
}

app.on("before-quit", (event) => {
  tier2LifecycleHandle?.stop();
  tier2LifecycleHandle = null;
  teardownIpcHandlers?.();
  teardownIpcHandlers = null;
  updateHandle?.stop();
  updateHandle = null;
  // Close the loopback broker; its per-boot token dies with it.
  if (capabilityService !== null) {
    void capabilityService.stopBroker().catch(() => {});
    capabilityService = null;
  }
  // Ordered shutdown: children (facade -> ai -> backend) then postgres.
  // preventDefault keeps the process alive until stop() resolves, then a
  // second quit passes straight through via the supervisorStopped flag.
  if (supervisor !== null && !supervisorStopped) {
    event.preventDefault();
    const active = supervisor;
    void active.stop().finally(() => {
      supervisorStopped = true;
      app.quit();
    });
  }
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
  signInWithGoogle(
    workspaceId: string,
  ): ReturnType<AuthService["signInWithGoogle"]>;
  signInWithWallet(
    workspaceId: string,
  ): ReturnType<AuthService["signInWithWallet"]>;
  linkGoogle(workspaceId: string): ReturnType<AuthService["linkGoogle"]>;
  linkWallet(
    workspaceId: string,
    confirmMerge: boolean,
  ): ReturnType<AuthService["linkWallet"]>;
  /**
   * User-initiated sign-out (renderer → IPC). Routes to the audited
   * signOutUserInitiated so a real sign-out is recorded; getSession eviction
   * uses the raw AuthService.signOut, which stays audit-free.
   */
  signOut(workspaceId: string): ReturnType<AuthService["signOutUserInitiated"]>;
  getSession(workspaceId: string): ReturnType<AuthService["getSession"]>;
  refresh(workspaceId: string): ReturnType<AuthService["refresh"]>;
  getBearer(workspaceId: string): Promise<string | null>;
  getBearerCachedSync(workspaceId: string): string | null;
  activeWorkspace(): string | null;
  /** Real install (no dev-mint, fail closed). Surfaced to the renderer. */
  isProductionPosture(): boolean;
}

function buildAuthService(
  authAudit: AuthAuditLog,
  facadeUrl: string | undefined,
  hostToken?: string,
): ActiveAuthService {
  // Production posture (real install, incl. CLI launch where app.isPackaged is
  // false) forces mode away from "dev-mint" so OidcClient can never mint the
  // "Sarah Chen" dev persona. Wallet + Google flows are mode-independent and
  // stay available. The dev-mint local sign-in is additionally hard-blocked in
  // the signIn wrapper below (defense in depth).
  const { productionPosture, mode } = resolveAuthPosture({
    isPackaged: app.isPackaged,
    env: process.env,
  });
  const explicitOidc = process.env.COPILOT_AUTH_MODE === "oidc";
  const facadeBaseUrl =
    facadeUrl ?? process.env.COPILOT_FACADE_URL ?? "http://127.0.0.1:8200";
  const devPersonaSlug = process.env.COPILOT_DEV_PERSONA ?? "sarah_acme";
  // Mirror the capability-store rule: the user's "file" secure-storage policy
  // makes plaintext (chmod-600) the sanctioned path, not a dev-only fallback.
  const allowPlaintext =
    process.env.BACKEND_ENVIRONMENT === "development" ||
    process.env.COPILOT_AUTH_MODE === "dev-mint" ||
    secureStorageMode === "file";

  let oidcConfig: ConstructorParameters<typeof AuthService>[0]["oidc"];
  // Only validate/build the OIDC provider config when a real OIDC provider was
  // explicitly requested. In production posture `mode` is "oidc" without any
  // provider env — that is intentional (it only disables dev-mint); signIn()
  // and refresh() then fail closed instead of minting a dev persona.
  if (explicitOidc) {
    const issuer = process.env.COPILOT_OIDC_ISSUER ?? "";
    const clientId = process.env.COPILOT_OIDC_CLIENT_ID ?? "";
    const authEp =
      process.env.COPILOT_OIDC_AUTHORIZATION_ENDPOINT ?? `${issuer}/authorize`;
    const tokenEp =
      process.env.COPILOT_OIDC_TOKEN_ENDPOINT ?? `${issuer}/token`;
    const scopes = (
      process.env.COPILOT_OIDC_SCOPES ?? "openid profile email"
    ).split(/\s+/u);
    if (issuer === "" || clientId === "") {
      throw new Error(
        "COPILOT_AUTH_MODE=oidc requires COPILOT_OIDC_ISSUER and COPILOT_OIDC_CLIENT_ID",
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
    hostToken,
    devPersonaSlug,
    oidc: oidcConfig,
    userDataDir: app.getPath("userData"),
    safeStorage: storesSafeStorage,
    openExternal: (url) => shell.openExternal(url),
    allowPlaintextFallback: allowPlaintext,
    authAudit,
  });

  return {
    // "Use locally, no account" — offered in every posture. In production
    // posture it mints the DEVICE ACCOUNT via the host-token-gated
    // /v1/auth/local/session (server-side singleton — same account across
    // restarts/reinstalls, D4-A; no local key material). In dev posture it
    // keeps the dev-mint path so the `make dev` flow is unchanged.
    signIn: (workspaceId) =>
      productionPosture
        ? service.signInLocal(workspaceId)
        : service.signIn(workspaceId),
    signInWithGoogle: (workspaceId) => service.signInWithGoogle(workspaceId),
    signInWithWallet: (workspaceId) => service.signInWithWallet(workspaceId),
    // Account-linking (PRD FR-L1/L2): authenticated LINK flows. The bearer is
    // pulled inside the service; only a renderer-safe outcome comes back.
    linkGoogle: (workspaceId) => service.linkGoogle(workspaceId),
    linkWallet: (workspaceId, confirmMerge) =>
      service.linkWallet(workspaceId, confirmMerge),
    // User-initiated sign-out: route to the audited variant so a real sign-out
    // emits a 'sign-out' audit row. The raw service.signOut used by getSession
    // eviction stays audit-free (no user-sign-out event on a silent eviction).
    signOut: (workspaceId) => service.signOutUserInitiated(workspaceId),
    getSession: (workspaceId) => service.getSession(workspaceId),
    refresh: (workspaceId) => service.refresh(workspaceId),
    getBearer: (workspaceId) => service.getBearer(workspaceId),
    getBearerCachedSync: (workspaceId) =>
      service.getBearerCachedSync(workspaceId),
    activeWorkspace: () => service.activeWorkspace(),
    isProductionPosture: () => productionPosture,
  };
}

// No facadeUrl (plain dev): MockTransport — explicit, never an implicit
// default. With a facadeUrl (supervised ready, or COPILOT_FACADE_URL in
// dev): WebTransport with the AuthService-backed bearer provider, wrapped
// with withBearerRefresh to retry once on 401 by calling
// authService.refresh. Auth audit events fire for the retry path.
function createTransport(
  authService: ActiveAuthService,
  auditLog: AuthAuditLog,
  facadeUrl: string | undefined,
): Transport {
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
    workspaceId: process.env.COPILOT_WORKSPACE_ID ?? "wsp_unknown",
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
