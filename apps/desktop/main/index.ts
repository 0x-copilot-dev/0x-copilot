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
  CAPABILITY_BROKER_AUDIENCE,
  DesktopWorkspaceAttestationPublisher,
  type CapabilityService,
} from "./capabilities";
// Direct subpath import (like the two workspace modules below): the boot path
// wants the gate's REASON, not just its boolean, so a disabled subsystem never
// looks like a bug and an unreadable flag value never looks like a decision.
import { resolveDesktopFilesystemGate } from "./capabilities/feature-gate";
import {
  createProductionWorkspaceAuthority,
  type WorkspaceAuthorityLifecycle,
} from "./capabilities/workspace-production-authority";
import {
  FacadeWorkspaceApprovalClient,
  WorkspaceApprovalHost,
  WorkspaceApprovalPermitSource,
  type WorkspaceApprovalNativeConfirmation,
} from "./capabilities/workspace-approval";
import { ConnectorService } from "./connectors/connector-service";
import { startCrashReporter } from "./crash-reporter";
import { registerDeepLinks } from "./deep-links";
import { registerIpcHandlers } from "./ipc/handlers";
import { applyBrandDockIcon, applyBrandIdentity } from "./branding";
import {
  createProductionDesktopBrowserSubsystem,
  type ProductionDesktopBrowserSubsystem,
} from "./browser/desktop-runtime";
import { resolveBrowserExecutablePath } from "./browser/browser-runtime";
import { isDesktopBrowserEnabled } from "./browser/feature-gate";
import { resolveAuthPosture } from "./posture";
import { installSingleInstance, shouldSupervise } from "./services/boot-mode";
import {
  loadOrCreateBootSecrets,
  setBootSecretsEncryption,
  type BootSecretsFs,
} from "./services/boot-secrets";
import { createDesktopSupervisor } from "./services/desktop-supervisor";
import { LocalServiceIdentityRegistry } from "./services/local-service-identity";
import { MacosWorkspaceConfinement } from "./services/macos-workspace-confinement";
import { BROWSER_BROKER_AUDIENCE } from "./browser/protocol";
import { resolveRuntimePaths } from "./services/runtime-paths";
import { applyBundledGoogleOAuth } from "./services/google-oauth-default";
import { SECURE_STORAGE_CHANNELS } from "./services/secure-storage-channels";
import { FIRST_RUN_CHANNELS } from "./services/first-run-channels";
import {
  loadFirstRunComplete,
  saveFirstRunComplete,
} from "./services/first-run-store";
import { registerOllamaDownloadIpc } from "./services/ollama-download";
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

const bootTimingStartedAt = process.hrtime.bigint();

function logBootTiming(phase: string): void {
  if (process.env.COPILOT_BOOT_TIMINGS !== "1") return;
  const elapsedMs =
    Number(process.hrtime.bigint() - bootTimingStartedAt) / 1_000_000;
  console.log(`[boot-timing] ${phase} ${elapsedMs.toFixed(1)}ms`);
}

logBootTiming("main");

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
// Created once per Electron boot. Only the relevant child's credential is put
// into its curated environment; main-owned brokers bind requests to it.
const supervisedServiceIdentities = new LocalServiceIdentityRegistry();
let supervisorStopped = false;
let browserSubsystem: ProductionDesktopBrowserSubsystem | null = null;
let browserSubsystemStopped = false;
let capabilityService: CapabilityService | null = null;
let workspaceAuthorityLifecycle: WorkspaceAuthorityLifecycle | null = null;
// C3 decision reservations are main-only. The private broker handoff consumes
// this typed source after C2 prepare; it is never installed in preload or
// renderer globals.
let workspaceApprovalPermitSource: WorkspaceApprovalPermitSource | null = null;
// AC9 — desktop connector OAuth service. Constructed once the facade is
// reachable (WebTransport mode). Held at module scope so the deep-link
// dispatcher (registered eagerly at boot) can route connector OAuth callbacks
// to it by state without re-registering the protocol handler.
let connectorService: ConnectorService | null = null;
// Held at module scope so the first-run IPC (registered eagerly at boot, BEFORE
// the facade is reachable and the auth service exists) can derive the
// per-account store key from the verified session once wireTransportAndIpc has
// built the service. Null until then; the first-run handlers fall back to the
// advisory workspaceId while it is null (see resolveFirstRunKey).
let activeAuthService: ActiveAuthService | null = null;
let activeFacadeBaseUrl: string | null = null;
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

/**
 * C3's native confirmation deliberately contains no renderer-supplied path,
 * title, target, or operation text. The host uses it for every approval until
 * a trusted destructive classification is available main-side.
 */
function buildWorkspaceApprovalConfirmation(): WorkspaceApprovalNativeConfirmation {
  return {
    async confirmApproval(): Promise<boolean> {
      const parent =
        mainWindow !== null && !mainWindow.isDestroyed()
          ? mainWindow
          : undefined;
      const options: Electron.MessageBoxOptions = {
        type: "warning",
        buttons: ["Cancel", "Approve"],
        defaultId: 1,
        cancelId: 0,
        title: "Confirm workspace change?",
        message: "Apply the reviewed workspace change?",
        detail:
          "Only the reviewed stage revision and target can be applied. " +
          "A separate workspace permission is required.",
        noLink: true,
      };
      const result = parent
        ? await dialog.showMessageBox(parent, options)
        : await dialog.showMessageBox(options);
      return result.response === 1;
    },
  };
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

/**
 * Electron main resolves a grant owner from an already-verified facade session.
 * A renderer workspace hint only selects a stored session; it never becomes an
 * authority claim or a persisted grant owner.
 */
async function resolveVerifiedWorkspaceProfileId(): Promise<string | null> {
  const auth = activeAuthService;
  const facadeBaseUrl = activeFacadeBaseUrl;
  const workspaceId = auth?.activeWorkspace();
  if (auth === null || facadeBaseUrl === null || workspaceId == null) {
    return null;
  }
  try {
    const bearer = await auth.getBearer(workspaceId);
    if (bearer === null) return null;
    const response = await fetch(
      `${facadeBaseUrl.replace(/\/+$/u, "")}/v1/me/profile`,
      {
        headers: { authorization: `Bearer ${bearer}` },
      },
    );
    if (!response.ok) return null;
    const payload = (await response.json()) as { readonly user_id?: unknown };
    const userId = payload.user_id;
    return typeof userId === "string" &&
      /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(userId)
      ? userId
      : null;
  } catch {
    return null;
  }
}

// Builds the capability service (grant store + native picker + loopback
// broker) and starts the broker. The broker token is minted per boot and is
// delivered OUT OF BAND to intended children (slice 2 wiring); it is never
// logged and never crosses renderer IPC. A broker failure must never block
// boot — the app is fully usable without host-folder grants.
async function startCapabilitySubsystem(
  workspaceConfinement: MacosWorkspaceConfinement | null,
): Promise<{
  /**
   * The child's READ credential for this boot. Non-null whenever the broker
   * bound — a subsystem that could not start returns null as a whole, so "no
   * broker" is already expressed. Writes are gated separately, one layer down;
   * see the comment at the return statement.
   */
  readonly workspaceBroker: {
    readonly baseUrl: string;
    readonly token: string;
    readonly audience: typeof CAPABILITY_BROKER_AUDIENCE;
  };
  /** Main-only signer; its private key never reaches a child process. */
  readonly workspaceAttestation: DesktopWorkspaceAttestationPublisher;
  /** The exact confinement instance used to launch the supervised children. */
  readonly workspaceConfinement: MacosWorkspaceConfinement | null;
} | null> {
  try {
    // Plaintext is legitimate in dev AND whenever the user's secure-storage
    // policy is "file" (the default) — the gated safeStorage reports
    // encryption unavailable then, and the store must not fail closed on the
    // user's own choice.
    const allowPlaintext =
      process.env.BACKEND_ENVIRONMENT === "development" ||
      process.env.COPILOT_AUTH_MODE === "dev-mint" ||
      secureStorageMode === "file";
    // C2 is only enabled for the supported packaged posture. The lifecycle
    // factory independently re-checks every condition and returns null on any
    // uncertainty; ordinary read grants and desktop boot remain available.
    if (workspaceConfinement !== null) {
      try {
        const secrets = await loadOrCreateBootSecrets({
          userDataDir: app.getPath("userData"),
          safeStorage: storesSafeStorage,
          fs: bootSecretsFs,
          mode: secureStorageMode,
        });
        workspaceAuthorityLifecycle = await createProductionWorkspaceAuthority({
          userDataDir: app.getPath("userData"),
          safeStorage: storesSafeStorage,
          installationSecret: secrets.vaultSecret,
          profileIdResolver: resolveVerifiedWorkspaceProfileId,
          confinement: workspaceConfinement,
          production: resolveAuthPosture({
            isPackaged: app.isPackaged,
            env: process.env,
          }).productionPosture,
          packaged: app.isPackaged,
        });
      } catch {
        workspaceAuthorityLifecycle = null;
      }
    }
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
      workspace: workspaceAuthorityLifecycle?.seed,
      localBrokerClients: [
        supervisedServiceIdentities.forBroker(
          "ai-backend",
          CAPABILITY_BROKER_AUDIENCE,
        ),
      ],
    });
    // C2 launch evidence is created from main-owned authority facts before
    // services start. It contains no path/grant/renderer input and is signed
    // with a one-boot key whose public half is injected only into ai-backend.
    const workspaceAttestation = new DesktopWorkspaceAttestationPublisher({
      attestation: capabilityService.workspaceWriteAttestation(),
    });
    // A disabled native/C2 authority does not even register an approval host:
    // recording an approval without a possible main-only permit path would
    // strand a stage and weaken the fail-closed launch gate.
    workspaceApprovalPermitSource = capabilityService.workspaceWritesAvailable()
      ? new WorkspaceApprovalPermitSource(capabilityService)
      : null;
    if (workspaceApprovalPermitSource !== null) {
      // D6/D7 private handoff: only Electron main retains the verified
      // approval reservation. The broker exposes no renderer-visible permit
      // or prepared reference and consumes the reservation after C2 prepare.
      capabilityService.installWorkspaceApprovalPermitHandoff(
        workspaceApprovalPermitSource,
      );
    }
    const handle = await capabilityService.startBroker();
    // baseUrl (host+port) is non-secret; the token is NOT logged.
    console.log("[capability-broker] listening on", handle.baseUrl);
    return {
      // READS and WRITES are two different authorities, and this credential is
      // the read one. It used to be withheld unless C2's writable host
      // authority existed — which is a macOS-only, packaged-only, native-helper
      // -only condition — so on Windows and in every dev run the supervised
      // ai-backend got RUNTIME_ENABLE_DESKTOP_WORKSPACE=false, built no
      // `/workspace/` mount, and the agent's `ls` fell through to its virtual
      // memory filesystem and reported real folders as empty. Withholding the
      // read lane never bought safety: a read is authorized by a GRANT the user
      // created in the native picker (the broker answers `grant_required` for
      // anything else), so a booted broker with no grants still exposes nothing.
      //
      // The write lane stays exactly as gated as it was, one layer down and
      // independent of this: without C2's authority `prepareChangeSet` fails
      // closed with `workspace_write_unsupported`, the legacy mutation routes
      // answer `capability_retired`, and no approval-permit handoff is
      // installed (see `workspaceApprovalPermitSource` above).
      workspaceBroker: {
        baseUrl: handle.baseUrl,
        token: capabilityService.brokerClientCredential("ai-backend"),
        audience: CAPABILITY_BROKER_AUDIENCE,
      },
      workspaceAttestation,
      workspaceConfinement:
        workspaceAuthorityLifecycle === null ? null : workspaceConfinement,
    };
  } catch (err) {
    await workspaceAuthorityLifecycle?.dispose().catch(() => {});
    workspaceAuthorityLifecycle = null;
    workspaceApprovalPermitSource = null;
    capabilityService = null;
    console.error("[capabilities] init failed (continuing without):", err);
    return null;
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
// identity is untrusted; we accept it ONLY as an advisory hint that selects
// WHICH session to look up (never as the store key itself, and never as an auth
// claim). The real key is derived from the verified session in resolveFirstRunKey.
function readWorkspaceId(payload: unknown): string | null {
  if (typeof payload === "object" && payload !== null) {
    const wid = (payload as Record<string, unknown>).workspaceId;
    if (typeof wid === "string" && wid.length > 0) return wid;
  }
  return null;
}

// Derive the per-account first-run store key from the VERIFIED session (its
// hashed claims.sub), resolved in main via AuthService.accountKey. The
// renderer-supplied workspaceId is advisory: it only selects which session to
// look up and is NEVER trusted as the key itself.
//
// Fallback: when no verified session has loaded yet — the very first paint
// before sign-in, or a cold auth service right at boot before
// wireTransportAndIpc runs — accountKey is null, so we fall back to the
// advisory workspaceId. This keeps the flag functional (rather than throwing)
// for a not-yet-authenticated read; the authenticated write that marks
// onboarding done later lands on the sub-keyed entry.
async function resolveFirstRunKey(workspaceId: string): Promise<string> {
  const key =
    activeAuthService === null
      ? null
      : await activeAuthService.accountKey(workspaceId);
  return key ?? workspaceId;
}

// Registers the first-run (FTUE) completion IPC. The renderer's FirstRunGate
// reads `get` to decide whether to show onboarding, and writes `set` when the
// user finishes/skips onboarding. Keyed per verified account (hashed claims.sub;
// see resolveFirstRunKey), persisted chmod-600. A read error yields
// `completed: false` so onboarding fails OPEN (never trap a user past onboarding
// on a bad read).
function registerFirstRunIpc(): void {
  ipcMain.handle(FIRST_RUN_CHANNELS.get, async (_event, payload) => {
    const workspaceId = readWorkspaceId(payload);
    if (workspaceId === null) return { completed: false };
    try {
      const accountKey = await resolveFirstRunKey(workspaceId);
      return {
        completed: loadFirstRunComplete(app.getPath("userData"), accountKey),
      };
    } catch (err) {
      console.error("[first-run] read failed:", err);
      return { completed: false };
    }
  });
  ipcMain.handle(FIRST_RUN_CHANNELS.set, async (_event, payload) => {
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
      const accountKey = await resolveFirstRunKey(workspaceId);
      saveFirstRunComplete(app.getPath("userData"), accountKey, completed);
      return { ok: true, completed };
    } catch (err) {
      console.error("[first-run] persist failed:", err);
      return {
        ok: false,
        error: err instanceof Error ? err.message : "unknown error",
      };
    }
  });
  // PRD-P8 §8 — the local-model card's "Get Ollama ↗". Argument-free by
  // design: the destination is a constant owned by main, so this cannot become
  // a generic "open any URL" escape hatch around the window-open denial below.
  registerOllamaDownloadIpc({
    ipcMain,
    openExternal: (url) => shell.openExternal(url),
  });
}

if (hasSingleInstanceLock) {
  void app.whenReady().then(async () => {
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
    // defensively so a broker bind failure never blocks boot.
    //
    // G4: gated on RUNTIME_ENABLE_DESKTOP_FILESYSTEM, resolved ONCE here (the
    // three former call sites read the same env three times) and now ON BY
    // DEFAULT. An explicit opt-out still wins and still fails closed: the
    // broker never binds, `capabilityService` stays null, the capability IPC
    // channels are never registered, so every capability call fails closed.
    //
    // Enabled is NOT the same as granted. What starts below is the broker, the
    // grant store and the native picker — the ability to ASK. Every read is
    // resolved against a grant the user created in that picker, so a booted
    // subsystem with no grants exposes no readable path (see the gate's module
    // header and the no-grants test in capabilities/feature-gate.test.ts).
    //
    // C2 supports only a signed packaged macOS runtime. The confinement object
    // is constructed from main-owned runtime paths and is handed to both the
    // authority launch gate and the actual supervisor; no renderer or service
    // can select its profile or weaken its rules.
    const filesystemGate = resolveDesktopFilesystemGate(process.env);
    console.log(`[capabilities] desktop filesystem ${filesystemGate.reason}`);
    let workspaceConfinement: MacosWorkspaceConfinement | null = null;
    if (filesystemGate.enabled && app.isPackaged) {
      try {
        const runtimePaths = resolveRuntimePaths({
          resourcesPath: process.resourcesPath,
          runtimeDirOverride: process.env.COPILOT_RUNTIME_DIR,
        });
        workspaceConfinement = new MacosWorkspaceConfinement({
          runtimeRoot: runtimePaths.runtimeRoot,
          webDir: runtimePaths.webDir,
          // Do not grant child Python the complete Electron userData tree:
          // capability grants, native helper journals/staging and Electron
          // settings stay main-only. These are the only child data roots
          // published by the desktop service-env contract.
          childDataDirs: [
            join(app.getPath("userData"), "agent-data", "v1"),
            join(app.getPath("userData"), "model-catalog"),
          ],
          temporaryDir: process.env.TMPDIR ?? "/tmp",
          pythonBin: runtimePaths.pythonBin,
          serviceDirs: [
            runtimePaths.serviceDir("backend"),
            runtimePaths.serviceDir("ai-backend"),
            runtimePaths.serviceDir("backend-facade"),
          ],
        });
      } catch (err) {
        console.error(
          "[workspace-confinement] setup failed; workspace writes remain unavailable:",
          err,
        );
      }
    }
    const capabilitySubsystem = filesystemGate.enabled
      ? await startCapabilitySubsystem(workspaceConfinement)
      : null;
    const workspaceBroker = capabilitySubsystem?.workspaceBroker ?? null;
    const workspaceAttestation =
      capabilitySubsystem?.workspaceAttestation ?? null;
    const verifiedWorkspaceConfinement =
      capabilitySubsystem?.workspaceConfinement ?? null;

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
      const supervisedEnv: NodeJS.ProcessEnv = { ...process.env };
      if (workspaceBroker !== null) {
        supervisedEnv.RUNTIME_ENABLE_DESKTOP_WORKSPACE = "true";
        supervisedEnv.DESKTOP_WORKSPACE_BROKER_URL = workspaceBroker.baseUrl;
        supervisedEnv.DESKTOP_WORKSPACE_BROKER_TOKEN = workspaceBroker.token;
        supervisedEnv.DESKTOP_WORKSPACE_BROKER_AUDIENCE =
          workspaceBroker.audience;
      } else {
        supervisedEnv.RUNTIME_ENABLE_DESKTOP_WORKSPACE = "false";
        delete supervisedEnv.DESKTOP_WORKSPACE_BROKER_URL;
        delete supervisedEnv.DESKTOP_WORKSPACE_BROKER_TOKEN;
        delete supervisedEnv.DESKTOP_WORKSPACE_BROKER_AUDIENCE;
      }
      if (workspaceAttestation !== null) {
        const bootstrap = workspaceAttestation.bootstrap();
        supervisedEnv.DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY =
          bootstrap.publicKey;
        supervisedEnv.DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD = bootstrap.payload;
        supervisedEnv.DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE =
          bootstrap.signature;
      } else {
        delete supervisedEnv.DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY;
        delete supervisedEnv.DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD;
        delete supervisedEnv.DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE;
      }
      if (isDesktopBrowserEnabled(process.env)) {
        try {
          const runtimePaths = resolveRuntimePaths({
            resourcesPath: process.resourcesPath,
            runtimeDirOverride: process.env.COPILOT_RUNTIME_DIR,
          });
          const browserExecutablePath = resolveBrowserExecutablePath({
            runtimeRoot: runtimePaths.runtimeRoot,
            executableOverride: process.env.BROWSER_EXECUTABLE_PATH,
          });
          browserSubsystem = createProductionDesktopBrowserSubsystem({
            userDataDir: app.getPath("userData"),
            workerEntryPath: join(
              __dirname,
              "..",
              "browser-worker",
              "index.js",
            ),
            electronExecutable: process.execPath,
            browserExecutablePath,
            processEnv: process.env,
            brokerClients: [
              supervisedServiceIdentities.forBroker(
                "ai-backend",
                BROWSER_BROKER_AUDIENCE,
              ),
            ],
            log: (message) => console.log(message),
            onStateChange: (state, reason) => {
              console.log(
                `[browser] ${state}${reason === undefined ? "" : ` (${reason})`}`,
              );
            },
          });
          const handle = await browserSubsystem.start();
          supervisedEnv.RUNTIME_ENABLE_DESKTOP_BROWSER = "true";
          supervisedEnv.DESKTOP_BROWSER_BROKER_URL = handle.baseUrl;
          supervisedEnv.DESKTOP_BROWSER_BROKER_TOKEN =
            browserSubsystem.broker.clientCredential("ai-backend");
          supervisedEnv.DESKTOP_BROWSER_BROKER_AUDIENCE =
            BROWSER_BROKER_AUDIENCE;
          // URL is loopback metadata; neither broker nor worker credential is
          // logged or exposed through renderer IPC.
          console.log("[browser] supervised broker is ready");
        } catch (err) {
          supervisedEnv.RUNTIME_ENABLE_DESKTOP_BROWSER = "false";
          delete supervisedEnv.DESKTOP_BROWSER_BROKER_URL;
          delete supervisedEnv.DESKTOP_BROWSER_BROKER_TOKEN;
          delete supervisedEnv.DESKTOP_BROWSER_BROKER_AUDIENCE;
          browserSubsystem = null;
          browserSubsystemStopped = true;
          console.error(
            "[browser] startup failed; capability remains unavailable:",
            err,
          );
        }
      } else {
        supervisedEnv.RUNTIME_ENABLE_DESKTOP_BROWSER = "false";
      }
      supervisor = createDesktopSupervisor({
        userDataDir: app.getPath("userData"),
        safeStorage,
        secureStorageMode,
        resourcesPath: process.resourcesPath,
        runtimeDirOverride: process.env.COPILOT_RUNTIME_DIR,
        processEnv: supervisedEnv,
        localServiceIdentities: supervisedServiceIdentities,
        workspaceChildConfinement: verifiedWorkspaceConfinement ?? undefined,
      });
      supervisor.onStatus((status) => {
        logBootTiming(status.phase);
        sendBootStatus(status);
      });
      supervisor
        .start()
        .then(({ facadeUrl, hostToken }) => {
          // The child verifies the bootstrap envelope before its in-process
          // worker starts. This facade publication is the runtime ingress for
          // a fresh signed statement and leaves a failed/missing bridge safely
          // unattested; it never delays or weakens normal desktop boot.
          if (workspaceAttestation !== null) {
            void workspaceAttestation
              .publish({ facadeBaseUrl: facadeUrl, hostToken })
              .catch((err: unknown) => {
                console.error(
                  "[workspace-attestation] publication failed; workspace writes remain unavailable:",
                  err,
                );
              });
          }
          wireTransportAndIpc(facadeUrl, hostToken);
        })
        .catch(async (err: unknown) => {
          // The supervisor already emitted a fatal BootStatus for the
          // renderer's fatal screen; keep the process alive so the user
          // can read it.
          if (browserSubsystem !== null && !browserSubsystemStopped) {
            await browserSubsystem.stop().catch(() => {});
            browserSubsystemStopped = true;
            browserSubsystem = null;
          }
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
  activeFacadeBaseUrl = facadeUrl ?? null;
  const auditLog = createFileAuthAuditLog({
    filePath: join(app.getPath("userData"), "audit", "auth.log"),
  });
  const authService = buildAuthService(auditLog, facadeUrl, hostToken);
  // Publish to module scope so the eagerly-registered first-run IPC can derive
  // the per-account store key from the verified session.
  activeAuthService = authService;
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

  // C3 does not use the generic transport bridge for local workspace
  // authority. This host is registered only when a real facade and an enabled
  // C2 main authority exist; MockTransport and unavailable native primitives
  // expose no approval channel.
  const workspaceApproval =
    facadeUrl === undefined ||
    capabilityService === null ||
    workspaceApprovalPermitSource === null ||
    !capabilityService.workspaceWritesAvailable()
      ? undefined
      : new WorkspaceApprovalHost({
          facade: new FacadeWorkspaceApprovalClient({
            facadeBaseUrl: facadeUrl,
            getBearer: async () => {
              const workspaceId = authService.activeWorkspace();
              return workspaceId === null
                ? null
                : authService.getBearer(workspaceId);
            },
          }),
          confirmation: buildWorkspaceApprovalConfirmation(),
          permits: workspaceApprovalPermitSource,
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
      cancelPendingSignIn: () => authService.cancelPendingSignIn(),
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
    workspaceApproval,
    // AC9 connector channels. Wired only against a real facade; returns only
    // the renderer-safe catalog + connection metadata (no provider token).
    connectors:
      connectorService === null
        ? undefined
        : {
            listCatalog: () => connectorService!.listCatalog(),
            authorize: (target) => connectorService!.authorize(target),
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
  if (workspaceAuthorityLifecycle !== null) {
    void workspaceAuthorityLifecycle.dispose().catch(() => {});
    workspaceAuthorityLifecycle = null;
  }
  workspaceApprovalPermitSource = null;
  // Ordered shutdown: children (facade -> ai -> backend), postgres, then the
  // browser broker/worker. Stopping ai-backend first ensures no new browser
  // request can race broker revocation.
  // preventDefault keeps the process alive until stop() resolves, then a
  // second quit passes straight through via the supervisorStopped flag.
  if (
    (!supervisorStopped && supervisor !== null) ||
    (!browserSubsystemStopped && browserSubsystem !== null)
  ) {
    event.preventDefault();
    const activeSupervisor = supervisor;
    const activeBrowser = browserSubsystem;
    void (async () => {
      if (activeSupervisor !== null && !supervisorStopped) {
        await activeSupervisor.stop().catch(() => {});
        supervisorStopped = true;
      }
      if (activeBrowser !== null && !browserSubsystemStopped) {
        await activeBrowser.stop().catch(() => {});
        browserSubsystemStopped = true;
        browserSubsystem = null;
      }
    })().finally(() => app.quit());
  }
});

app.on("window-all-closed", () => {
  // When we supervise an embedded PostgreSQL + the Python services, closing the
  // last window MUST quit on EVERY platform so `before-quit` tears the children
  // and the postmaster down. Keeping a headless supervised app alive after the
  // window closes (the macOS keep-in-dock convention) is what left orphaned
  // Electron/postgres pids on CLI launches — `copilot` never returned. In
  // non-supervised dev (MockTransport, no children) keep the standard macOS
  // behavior.
  if (process.platform !== "darwin" || supervisor !== null) {
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
  /** Cancel the pending system-browser sign-in (wallet or Google). */
  cancelPendingSignIn(): void;
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
  /**
   * Stable, non-reversible key derived from the VERIFIED session's claims.sub,
   * used to namespace main-process UX flags (first-run) per account. Null when
   * no verified session is loaded.
   */
  accountKey(workspaceId: string): Promise<string | null>;
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
    cancelPendingSignIn: () => service.cancelPendingSignIn(),
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
    accountKey: (workspaceId) => service.accountKey(workspaceId),
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
